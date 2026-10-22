# LinkedIn AI Content Agent

Single-owner Django app that plans, generates, reviews and (after explicit owner approval)
publishes LinkedIn posts through LinkedIn's official API. Built milestone by milestone from
the implementation brief.

- Source of truth: [docs/IMPLEMENTATION_BRIEF.md](docs/IMPLEMENTATION_BRIEF.md)
- Decisions and deviations: [docs/DECISIONS.md](docs/DECISIONS.md)

**Status:** Application workflows through milestone 12 are implemented; production launch checks
and live LinkedIn verification remain dependent on owner-managed credentials, app approval, and
hosting resources. See [deployment runbook](docs/DEPLOYMENT.md).

## Stack

| Area | Choice |
|---|---|
| Language | Python 3.13 (managed by `uv`, see `.python-version`) |
| Web | Django 5.2 LTS, server-rendered templates, Bootstrap 5.3, HTMX |
| Database | PostgreSQL 16+ (local: PostgreSQL 18) |
| Background work | Celery 5.6 with Redis (local on Windows: Memurai) |
| Quality | pytest, pytest-django, Ruff, mypy + django-stubs, Bandit, pip-audit |

## Layout

```text
config/           settings (base/dev/test/prod), URLs, Celery app
apps/core/        health endpoints, home page, audit log, token encryption (security.py)
apps/accounts/    LinkedIn account (encrypted tokens), owner check
apps/content/     weighted strategy groups, pillars, topics, verified knowledge, writing examples
apps/posts/       posts, reviews, versions, publications, Celery tasks, status state machine
apps/linkedin/    LinkedIn OAuth, member identity, official Posts API client and publish task
apps/ai/          Gemini API draft generation and quality review using verified knowledge
templates/        base.html (Bootstrap + HTMX) and page templates
static/vendor/    pinned Bootstrap and HTMX files (see VENDOR.md)
tests/            pytest suite
knowledge/        founder/company facts, writing style, real writing examples (Markdown)
docker/           Dockerfile and Compose web, worker, Beat, PostgreSQL and Redis services
docs/             implementation brief, decisions, and deployment/recovery runbook
```

Django's built-in admin is mounted at `/django-admin/` so `/admin/` is free for the owner dashboard.

## First-time setup (Windows)

Prerequisites: [uv](https://docs.astral.sh/uv/), PostgreSQL 16+ running locally, and Redis
(Memurai Developer on Windows). With Docker instead, after creating `.env` (step 2), run
`docker compose --env-file .env -f docker/docker-compose.yml up -d` and skip step 1.

1. Create the database user and database (once, as the `postgres` superuser):

   ```sql
   CREATE ROLE linkedin_agent WITH LOGIN PASSWORD '...' CREATEDB;
   CREATE DATABASE linkedin_agent OWNER linkedin_agent;
   ```

   `CREATEDB` lets the test suite create its own `test_linkedin_agent` database.

2. Copy `.env.example` to `.env` and fill in `DJANGO_SECRET_KEY`, `DB_PASSWORD` and `TOKEN_ENCRYPTION_KEYS`.

3. Install dependencies into `.venv` from the lockfile and migrate:

   ```powershell
   uv sync
   uv run python manage.py migrate
   uv run python manage.py sync_knowledge
   uv run python manage.py createsuperuser
   ```

## Running locally

Use two terminals:

```powershell
# Terminal 1: web
uv run python manage.py runserver

# Terminal 2: Celery worker (--pool=solo is required on Windows)
uv run celery -A config worker --pool=solo -l info

# Terminal 3: optional Celery Beat scheduler (queues two owner-topic drafts at 08:00 daily)
uv run celery -A config beat -l info
```

- App: http://localhost:8000/ (requires login as the owner/superuser)
- Liveness: http://localhost:8000/health/
- Readiness (checks PostgreSQL and Redis): http://localhost:8000/health/ready/

## Knowledge base

The AI uses verified knowledge stored in the database. Markdown files in `knowledge/` are one
source for those records:

- `founder.md`, `company.md`: one fact per bullet under `## Facts`, each ending with
  `(source: ...)`. Bullets without a source are stored as unverified and never used.
- `writing_style.md`: voice rules, the standard LinkedIn post format, post formats and the
  approved subject areas.
- `writing_examples/`: real posts the founder wrote, one file each, with `approved: true`
  (see the README in that folder). Never add AI-generated posts.

After editing any of these, run `uv run python manage.py sync_knowledge`. Records can also be
reviewed in Django admin (`/django-admin/`); facts that came from the files are overwritten by
the next sync, so change them in the files.

The owner can also update founder/company facts, writing rules, and approved subject areas from
the **Knowledge** link in the app navigation (`/knowledge/`). Those entries are stored separately
in PostgreSQL and are not overwritten by `sync_knowledge`. Founder and company facts must include
a source on each line using `fact | source`; only verified, active facts are supplied as factual
support to Gemini. Dashboard-managed values take precedence over Markdown-imported values within
their category; clearing a dashboard section restores the Markdown values for that category.

## MVP demo flow

Sign in as the owner, enter a keyword on the home page, and generate a draft. The app first selects
and stores a strategy-backed, nonduplicate topic, then asks Gemini to write the post for that exact
topic using verified knowledge, approved examples, and recent posts. The draft enters the manual
review queue.
The owner can edit, approve, or reject it; edits are versioned and the original AI output is kept.
When approving, the owner chooses a date and a start/end time window. The app randomly assigns a
future time within that window and displays it in the drafts list and post detail page. The owner
can edit or cancel the assigned time before publishing starts. Beat queues the approved post, and
the worker publishes it through LinkedIn's official API when due.
At 08:00 daily in the configured owner timezone, Beat queues two drafts using different topics
from the owner's **Morning topics** list. Scheduled jobs create drafts only. Before using live
generation, set `GEMINI_API_KEY` in the ignored `.env` file. `GEMINI_MODEL` defaults to
`gemini-3.8-flash`. Never paste an API key into chat or commit it.

Each draft receives one generated version and a structured quality review. The draft then goes
to the owner for manual review, even when the AI review flags quality issues; the owner can use
the explicit Regenerate action to request another version, within the three-attempt lifetime cap.
AI review never approves or publishes. The morning job creates two separate drafts from the saved
topic pool. Manage the list from **Morning topics** in the navigation: add topics individually or
remove them one at a time. Keep at least two topics in the pool so the morning job can choose two
different ones.

### Run the morning job without Celery or Redis

On Windows, the direct command creates today's drafts synchronously and does not use a worker,
Beat, or Redis:

```powershell
$env:DJANGO_SETTINGS_MODULE = "config.settings.prod"
uv run python manage.py generate_morning_drafts
```

The production settings use the database values from `.env`. For an external Render database, the
laptop must be online, the database must allow the laptop's current public IP, and the production
environment values (including Gemini key and token encryption key) must be configured locally.
The command is idempotent for the day: it won't create duplicate drafts for slots already running
or completed. It prints the outcome; the log wrapper stores output under
`%LOCALAPPDATA%\LinkedInAIContentAgent\logs\morning-drafts.log`.

To rehearse it immediately with a database/topic check and a confirmation prompt, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\test_morning_drafts.ps1"
```

Type `RUN` when prompted. The script shows the database host, saved topics, and today's slot state,
then creates drafts synchronously and prints each result. If a slot fails, it retries only the failed
slot, up to three total attempts. To have those drafts appear on Render,
the local `.env` must point to the same Render PostgreSQL database used by the web app, and that
database must allow your laptop's IP. This rehearsal uses Gemini credits/quota but does not publish.
The lower-level Task Scheduler wrapper, `scripts/run_morning_drafts.ps1`, runs without a prompt.

To run it every day at 8:00 a.m. without Celery services, create a Windows Task Scheduler task:

1. Open **Task Scheduler** → **Create Basic Task**. Name it `LinkedIn morning drafts`.
2. Choose **Daily**, set the start time to **8:00:00 AM**, and choose **Start a program**.
3. Program: `powershell.exe`
4. Arguments: `-NoProfile -ExecutionPolicy Bypass -File "C:\path\to\Linkedin AI content agent\scripts\run_morning_drafts.ps1"`
5. Replace the example path with this repository's actual path, then finish and enable the task.

The computer must be awake and connected to the internet at that time. If it is asleep, enable
**Wake the computer to run this task** in the task's Conditions. Configure the task to run under
the Windows account that owns this repository and its `.env` file. The script uses the same
database and AI generation flow as the Beat task; it doesn't publish posts.

Google AI Studio offers a free Gemini API tier for eligible models, subject to changing quotas.
Google may use content submitted through the unpaid tier to improve its products. Do not include
confidential or sensitive personal information in generation prompts; check the current [Gemini
API pricing and data terms](https://ai.google.dev/gemini-api/docs/pricing) before production use.

## Checks (run before finishing any milestone)

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy apps config
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run pytest
uv run bandit -c pyproject.toml -r apps config -q
uv export --format requirements-txt --no-emit-project -o requirements-audit.txt
uv run pip-audit -r requirements-audit.txt --require-hashes --disable-pip
```

CI (`.github/workflows/ci.yml`) runs the same steps against PostgreSQL and Redis service containers.
Automated tests never call Gemini or LinkedIn.

## Secrets

- Real values live only in `.env` (git-ignored) or the hosting secret manager.
- `.env.example` holds placeholders only.
- LinkedIn tokens are encrypted at rest (Fernet: AES + HMAC) with `TOKEN_ENCRYPTION_KEYS`, never the
  Django `SECRET_KEY`. In code they are masked `SecretValue` objects and never appear in admin,
  templates, logs or `dumpdata`. Back up the database with `pg_dump`.

### Rotating the token encryption key

1. Generate a new key and put it **first** in `TOKEN_ENCRYPTION_KEYS`, keeping the old key after it
   (`TOKEN_ENCRYPTION_KEYS=<new>,<old>`), then restart the web process and worker.
2. Run `uv run python manage.py rotate_token_keys` to re-encrypt stored tokens with the new key.
3. Remove the old key (`TOKEN_ENCRYPTION_KEYS=<new>`) and restart again.
