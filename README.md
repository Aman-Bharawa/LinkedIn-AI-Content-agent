# LinkedIn AI Content Agent

Single-owner Django app that plans, generates, reviews and (after explicit owner approval)
publishes LinkedIn posts through LinkedIn's official API. Built milestone by milestone from
the implementation brief.

- Source of truth: [docs/IMPLEMENTATION_BRIEF.md](docs/IMPLEMENTATION_BRIEF.md)
- Decisions and deviations: [docs/DECISIONS.md](docs/DECISIONS.md)

**Status:** Milestone 0 — project foundation.

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
apps/core/        health endpoints and home page
templates/        base.html (Bootstrap + HTMX) and page templates
static/vendor/    pinned Bootstrap and HTMX files (see VENDOR.md)
tests/            pytest suite
docker/           Docker Compose for local PostgreSQL and Redis
docs/             implementation brief and decisions log
```

Domain apps (`accounts`, `content`, `posts`, `ai`, `linkedin`) are added in their milestones.
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

2. Copy `.env.example` to `.env` and fill in `DJANGO_SECRET_KEY` and `DB_PASSWORD`.

3. Install dependencies into `.venv` from the lockfile and migrate:

   ```powershell
   uv sync
   uv run python manage.py migrate
   uv run python manage.py createsuperuser
   ```

## Running locally

Use two terminals:

```powershell
# Terminal 1: web
uv run python manage.py runserver

# Terminal 2: Celery worker (--pool=solo is required on Windows)
uv run celery -A config worker --pool=solo -l info
```

- App: http://localhost:8000/ (requires login as the owner/superuser)
- Liveness: http://localhost:8000/health/
- Readiness (checks PostgreSQL and Redis): http://localhost:8000/health/ready/

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
Automated tests never call OpenAI or LinkedIn.

## Secrets

- Real values live only in `.env` (git-ignored) or the hosting secret manager.
- `.env.example` holds placeholders only.
- LinkedIn tokens will be encrypted with a dedicated key (`TOKEN_ENCRYPTION_KEYS`), never the Django `SECRET_KEY`.
