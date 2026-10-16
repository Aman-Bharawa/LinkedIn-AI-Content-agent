# LinkedIn AI Content Agent

## Python Implementation Roadmap and Developer Requirements


This document turns the supplied product blueprint into an incremental implementation plan for a Python application. It is intended to be the source brief for the developer and should be implemented one milestone at a time.

> **Owner amendments (2026-10-14).** The owner changed the workflow as allowed by §12. The sections
> below are updated in place; the original text is in git history (tag `milestone-2`).
>
> 1. **Keyword posts.** The owner can type a keyword/topic and the AI writes a post about it.
>    Leaving it blank lets the AI choose the topic.
> 2. **Daily auto-draft expanded.** The weekday 08:00 job creates three different AI-chosen drafts.
> 3. **Origin tags.** Every post is tagged by origin — **Keyword** (owner keyword) or
>    **Auto draft** (scheduled job) — and the tag is shown in the dashboard.
> 4. **Approval queue.** Posts awaiting approval form a sequence. Rejecting a post removes it
>    from the sequence (it is kept in history, not deleted).
> 5. **Publishing stays manual, as originally written.** The owner publishes an approved post
>    themselves. No scheduled job ever publishes.

## 1. Project objective

Build a single-owner application that helps user's plan, generate, review, edit, and publish LinkedIn posts. The system can select a topic and draft content automatically, but a person must explicitly approve every post before it can be published in V1.

Expected V1 flow:

```text
Owner keyword request (origin: Keyword)  or  weekday 08:00 auto-draft job (origin: Auto draft)
  -> select a topic and pillar (from the keyword, or chosen by the AI)
  -> load verified knowledge and writing guidance
  -> generate a structured post
  -> review and, if needed, regenerate (maximum three attempts)
  -> save the draft into the approval queue (pending approval)
  -> User edits, rejects (removed from the queue), or approves it
  -> the owner publishes an approved post through LinkedIn's official API
  -> retain the LinkedIn post ID and publication record
```

Publishing must be impossible unless the post is in the approved state. AI approval is never a substitute for the owner's approval.

## 2. Scope and implementation assumption

This roadmap assumes a **fresh Python implementation**. The existing Rails project is a prototype and product reference, not a runtime dependency. The developer should not build a mixed Rails/Python application. Before implementation, inspect the Rails repository for useful decisions and tests; migrate existing user data only if the owner confirms that data exists and must be retained.

The initial product is for a User only. Do not build a multi-user SaaS product or support multiple LinkedIn accounts in V1.

## 3. Recommended Python stack

Use a conventional, maintainable monolith. Pin exact dependency versions in a lockfile and confirm that all selected packages support the chosen Python/Django versions before the first implementation commit.

| Area | Recommended choice | Requirement |
|---|---|---|
| Language | Python 3.13 | Use a supported Python release; record it in `.python-version` and CI. |
| Web framework | Django 5.2 LTS, latest supported patch | Django provides authentication, ORM, migrations, forms, CSRF protection, and admin foundations. Django's published support table currently lists extended support for 5.2 LTS through April 2028. Recheck at project kickoff. |
| Database | PostgreSQL 16 or newer supported release | Use relational constraints and transactions for post state and publishing safety. Enable `pg_trgm` when implementing similarity-based duplicate checks. |
| Background work | Celery 5.6 stable with Redis 7 | Run API calls and scheduled draft generation outside web requests. Keep publisher retries separate from generation retries. |
| Web UI | Django templates with a small amount of HTMX or plain JavaScript | Build the review workflow server-side. React is not required. |
| Gemini | Official Google GenAI Python SDK and Interactions API | Use JSON Schema structured output for topic, post, and review results. Keep model ID configurable. |
| LinkedIn | Official LinkedIn OAuth 2.0 Authorization Code flow and Posts API | Confirm the app has the required LinkedIn product access and scopes before promising publication. Do not automate browser posting. |
| Tests and quality | pytest, pytest-django, Ruff, mypy where useful, Bandit, pip-audit | Run these in CI and before completing each milestone. |
| Local environment | Docker Compose | Start PostgreSQL and Redis locally. Keep production configuration external to the repository. |

Suggested project layout:

```text
config/                 Django settings, URLs, Celery setup
apps/
  accounts/             owner authentication and LinkedIn account
  content/               topics, knowledge, writing examples
  posts/                 drafts, reviews, versions, publication history
  ai/                    topic selection, generation, review, prompts
  linkedin/              OAuth, API client, publishing service
templates/               authenticated review dashboard
knowledge/               founder/company/style Markdown source files
tests/                   model, service, task, request, and workflow tests
docker/                  local development support
```

The developer may choose another conventional layout if they document it before implementation. Keep business rules in domain services, not views or templates.

## 4. Verified profile and content strategy

### Verified facts

These are the only initial personal/company facts supplied as verified. Do not invent biography, location, services, customers, projects, revenue, company results, conversations, or personal stories. The owner must provide and verify any additional facts before they enter the source of truth.

### Approved subject areas

Software development; startup product development; engineering teams; AI; Ruby on Rails; full-stack development; startup technology; offshore development; founder lessons; technology business; MENA startup ecosystem; India engineering teams; and product development.


### Configurable pillars and target distribution

Store pillar names and target shares in configuration or editable strategy records, not scattered constants in application code. The initial weighted groups are:

| Content group | Target share |
|---|---:|
| Founder/business | 30% |
| AI/technology | 25% |
| Startup/product | 20% |
| Engineering/team | 15% |
| company | 10% |

Initial pillars:

1. Founder lessons
2. Startup product development
3. AI and software development
4. Engineering leadership
5. Developer productivity
6. Hiring and team building
7. Client lessons
8. Technology business
9. Startup mistakes
10. Product strategy
11. MENA startup ecosystem
12. India engineering ecosystem
13. Company lessons

The developer should map each pillar to one distribution group and divide that group's share among its pillars unless the owner supplies more detailed weights. Do not present a client lesson or Company case study as factual without a verified source.

## 5. Data model requirements

Use Django models and database migrations. Add constraints and indexes in PostgreSQL as well as application validation. Use `JSONField` for structured values such as scopes and hashtags only where appropriate; do not store core business state as an opaque JSON document.

### `LinkedInAccount`

- LinkedIn member identifier/URN
- encrypted access token
- encrypted refresh token, when LinkedIn grants one
- token expiration timestamp
- granted scopes
- active/connected state
- created and updated timestamps

Tokens must never appear in HTML, API responses, exception messages, or logs. Use authenticated encryption with a dedicated key held outside the database and support key rotation. Do not reuse Django's `SECRET_KEY` as the token encryption key.

### `LinkedInPost`

- topic and content pillar
- origin: `keyword` (owner keyword) or `auto` (scheduled auto-draft), plus the owner's keyword when given
- current content and hook
- hashtags
- status: `draft`, `generating`, `reviewing`, `pending_approval`, `rejected`, `approved`, `publishing`, `published`, `failed`, `needs_manual_review`
- AI review score and notes
- rejection reason
- LinkedIn post ID/URN
- scheduled and published timestamps
- manually-edited flag
- original AI-generated content
- prompt/model version metadata
- created and updated timestamps

Add indexes for status, scheduled time, published time, and other fields used by dashboard filters. Treat status transitions as an explicit state machine; validate transitions in domain services.

### `ContentTopic`

- title, description/angle, pillar
- used count, last-used timestamp, active flag
- timestamps

Prevent exact normalized-title duplicates in PostgreSQL. Track selections and post history so the selector can avoid recent ideas.

### `ContentKnowledge`

- title, content, category, source
- verified and active flags
- timestamps

Only active, verified knowledge may be used as factual support. Retain source/provenance so the owner can audit or correct it.

### `WritingExample`

- title, content, category, active flag
- timestamps

Only authentic, founder-approved examples may be used as writing samples. Do not seed generated posts as genuine examples.

### `PostReview`

- foreign key to post
- authenticity, usefulness, originality, founder voice, AI-like, promotional tone, clarity, and overall scores
- approved-by-AI flag, feedback, problems, improvement suggestions
- reviewer model/prompt version and timestamp

Define score direction explicitly: quality scores are higher-is-better; AI-like and promotional-risk scores are higher-is-worse. Configure thresholds and test their interpretation.

### Supporting records

- `PostVersion`: preserve the initial AI draft and subsequent owner edits; never overwrite the original output.
- `PostPublication`: record each publishing attempt, status, timestamps, HTTP status, safe error details, and resulting LinkedIn ID. Enforce one active publication attempt per post and make operations idempotent.
- Optional `AuditEvent`: record owner actions such as edit, reject, approve, retry, connect, and disconnect without storing secrets.

## 6. Milestones

Each milestone is a reviewable increment. Do not start the next milestone until the current one meets its acceptance criteria, tests pass, and the owner has had a chance to review it.

### Milestone 0 — Python project foundation

**Build:** Django project, settings split by environment, PostgreSQL, Redis, Celery bootstrap, environment-variable loading, Docker Compose for local dependencies, health endpoint, pytest, Ruff, security checks, `.env.example`, CI, and setup documentation.

**Acceptance:** A developer can install from the lockfile, start PostgreSQL/Redis, migrate the database, run the web process and worker, view the health endpoint, and run tests. No API or LinkedIn credentials are committed.

### Milestone 1 — Database schema

**Build:** The models and migrations listed above, constraints, indexes, relationships, encryption configuration, and model tests.

**Acceptance:** Migrations create a clean database; validations, indexes, encryption/decryption, and allowed status values are covered by tests. No token is exposed through serializers or admin list/detail pages.

### Milestone 2 — Knowledge base

**Build:** `knowledge/founder.md`, `knowledge/company.md`, `knowledge/writing_style.md`, and `knowledge/writing_examples/`. Add a loader that returns verified facts, approved topics, writing rules, and approved examples to AI services. Add an owner workflow to update/review knowledge later.

**Writing rules:** Short paragraphs; direct, practical, conversational voice; avoid corporate buzzwords, generic motivation, clickbait, and excessive emojis; do not fabricate facts or personal experiences; CTA is optional.

**Acceptance:** Loader tests prove that supplied facts and style are included and that missing/empty examples are handled. Unsupported claims are never elevated into verified knowledge.

### Milestone 3 — Content strategy configuration

**Build:** Editable or configuration-backed groups, weights, the 13 initial pillars, active/inactive controls, and tests that weights total 100% and all pillars map to a group.

**Acceptance:** Changing target shares or deactivating a pillar does not require editing AI service code.

### Milestone 4 — AI topic selection

**Build:** `Ai.select_topic` service using the configured strategy, active verified knowledge, recent posts/topics, an optional owner keyword, and Gemini structured output. With a keyword, the topic must be about that keyword; without one, the AI chooses. Return JSON fields `pillar`, `topic`, `angle`, and `format`. Avoid recently used topics, exact and near duplicates, unsupported personal stories, and excessive promotion. Record accepted topics and cap retries.

**Acceptance:** With a mocked API response, the service returns a schema-valid configured pillar and a new topic; tests cover duplicate rejection, invalid output, missing key, API errors, and bounded retry. Do not make live API calls in automated tests.

### Milestone 5 — AI post generation

**Build:** `Ai.generate_linkedin_post` accepts a selected topic, pillar, verified knowledge, style guide, approved writing examples, and recent posts. Return structured `topic`, `hook`, `post`, and `hashtags`. Keep prompts versioned and record the model/prompt version with output.

**Generation constraints:** No invented personal experience, clients, metrics, revenue, conversations, or results; no copying samples; no unsupported company claims; avoid excessive company promotion; short practical paragraphs; useful without a CTA.

**Acceptance:** Valid structured content is stored as the original generated version with a non-published initial status. API failures do not create a publishable post.

### Milestone 6 — AI quality review and bounded regeneration

**Build:** `Ai.review_linkedin_post` evaluates authenticity, founder voice, usefulness, originality, clarity, AI-like writing, promotional tone, unsupported claims, repetition, and hook quality. Save every review. Regenerate when configured thresholds fail, an unsupported claim is found, or AI-like risk is too high.

**Regeneration rules:** Maximum three generation attempts. After the final failed review, set `needs_manual_review` and show feedback. Reviewers never publish posts.

**Acceptance:** Review output validates against a schema; threshold and score-direction tests pass; no retry loop can run indefinitely; unsupported claims always block automatic movement to approval.

### Milestone 7 — Authenticated review dashboard

**Build:** Owner-only `/admin/posts` dashboard for pending approval, drafts, published, rejected, failed, and needs-manual-review posts. Display topic, pillar, origin tag (Keyword / Auto draft), score, post, hashtags, and review feedback. Include a keyword form to request a new post and the approval queue. Actions: edit, regenerate, reject, approve. Protect every page and action with authentication and authorization. No public signup is needed for the single-owner V1.

**Acceptance:** Unauthenticated requests are denied. The owner can inspect a saved draft and reject it. The dashboard exposes no API or OAuth tokens.

### Milestone 8 — Editing and history

**Build:** Edit topic, hook, content, and hashtags. Mark manual edits, preserve the original AI output, and store versions for comparison. Record final content used for publication.

**Acceptance:** Editing never overwrites the original output; tests verify edit history, ownership checks, and preservation of the final approved version.

### Milestone 9 — LinkedIn OAuth

**Build:** `/linkedin/connect` and `/linkedin/callback` using LinkedIn's official member Authorization Code flow. Use state validation, exact redirect URI checks, minimum requested scopes, secure token exchange, member identity retrieval, encrypted token storage, and disconnect/revoke handling where supported.

**Acceptance:** A real account can connect only after the LinkedIn application has the required product access and scopes. OAuth failure leaves no partial active account. Never ask for a LinkedIn password or use Selenium/Playwright/browser login automation.

### Milestone 10 — Official LinkedIn publishing

**Build:** `LinkedIn.publish_post` service and publication task. Fetch the active account server-side, validate token and post state, submit to the official Posts API, persist response ID/URN and publication timestamp, and record safe failure information.

**Safety and idempotency:** Only `approved` posts may transition to `publishing`; lock the row/attempt transactionally to prevent concurrent publication. Never blindly retry an ambiguous timeout that might have published. Resolve uncertain outcomes through safe reconciliation or hold for manual review. Do not log bearer tokens or full authorization headers.

**Acceptance:** An owner-approved post publishes through LinkedIn's official API; response ID is saved; duplicate task delivery cannot create another post; rejected, failed, or unapproved posts cannot publish. Pin `LINKEDIN_API_VERSION` from current documentation and review version sunsets before release.

### Milestone 11 — Background processing

**Build:** Celery tasks for generation, review, regeneration, and publishing, with explicit timeouts, error handling, task status, and bounded retries. Route outbound publishing through a single controlled task. Use task deduplication/locks for generation and publishing.

**Acceptance:** Queue retries do not bypass post-state rules; worker loss or API failures leave a recoverable status and useful owner-facing error message.

### Milestone 12 — Scheduler and dashboard notifications

**Build:** Celery Beat schedule, initially weekdays at 08:00 in the configured owner timezone, that selects three distinct topics and queues three reviewed drafts (origin `auto`, tagged "Auto draft"). Add dashboard notifications for each result. No scheduled action may publish.

**Acceptance:** Scheduler creates at most three distinct-topic drafts per configured run, respects timezone and duplicate safeguards, and never approves or publishes a draft.

### Milestone 13 — Production hardening and deployment

**Build:** Containerized Django web process, Celery worker, Celery Beat, managed PostgreSQL and Redis, HTTPS, secure cookies, backups/restore procedure, structured logs with secret filtering, health/readiness checks, rate limiting, dependency scanning, and deployment runbook.

**Acceptance:** `manage.py check --deploy` is clean or has documented justified exceptions; recovery/restore is tested; secrets are provided through the hosting secret manager; admin routes require authentication; API timeouts and failures are handled.

## 7. Critical workflow and safety tests

The following are release-blocking tests:

1. AI generation failure never creates a published post.
2. AI review failure never publishes.
3. Rejected posts cannot publish.
4. Only owner-approved posts can publish.
5. Failed LinkedIn requests are recorded without leaking tokens.
6. LinkedIn access/refresh tokens never appear in templates, API payloads, or logs.
7. Duplicate task delivery cannot publish the same post twice.
8. Regeneration stops after three attempts.
9. Manual edits and original AI content are both preserved.
10. Unauthenticated users cannot access dashboard routes or actions.
11. Unsupported claims block movement to owner approval or are clearly flagged for manual review.
12. Scheduled tasks create drafts only; they never publish.

Use mocked Gemini and LinkedIn clients in unit/integration tests. Live API verification belongs in an explicit manual test environment and must never run as part of the normal test suite.

## 8. Security and privacy requirements

- HTTPS in production; secure, HTTP-only, same-site session cookies.
- Django CSRF protection on all state-changing browser actions.
- Owner-only authentication and object/action authorization.
- No public account signup for V1.
- Secrets only in local ignored `.env` files or the deployment secret manager; provide placeholders in `.env.example` only.
- Encrypt LinkedIn tokens at rest with a dedicated key and a documented rotation procedure.
- Never expose tokens in templates, JavaScript, API responses, logs, error trackers, or analytics.
- Apply request timeouts, safe error messages, and rate limits to external integrations.
- Use minimum LinkedIn scopes; do not collect or store information not required for the workflow.
- Treat model output as untrusted. Validate schemas and business rules in application code.
- Keep human approval mandatory. Do not add a hidden auto-publish path.

## 9. Explicit V1 non-goals

Do not implement automatic publication without owner approval, browser automation, LinkedIn scraping, password collection, multi-user SaaS, multiple LinkedIn accounts, autonomous comments/likes/connections/DMs, engagement manipulation, vector databases, advanced analytics, or automatic prompt changes based on one edit.

## 10. Later phases (after V1)

### Phase 2

- Content calendar at `/admin/calendar` with date, pillar, topic, status, scheduled time, and published time; allow reschedule, approve, reject, edit, and regenerate.
- LinkedIn analytics if account/API permissions allow it: impressions, reactions, comments, reposts, engagement rate, profile visits, inbound leads.
- Analyze owner edits after approximately 30–50 approved posts. Identify writing preferences, but do not change the production prompt without review and enough examples.
- Stronger PostgreSQL near-duplicate checks for topics, hooks, conclusions, and angles; use configured similarity thresholds and tests.
- Email or messaging notifications only after dashboard workflow is stable.
- Image and carousel generation only after text workflow quality is established.

### Phase 3 — Optional controlled automatic publishing

Only after demonstrated quality and explicit owner authorization, consider a separately gated workflow:

```text
topic -> generate -> review -> duplicate check -> policy/safety check -> publish
```

Default `AUTOMATIC_PUBLISHING_ENABLED=false`. Provide a global kill switch. Keep the human-approval flow available and require an audit trail for any change to this policy.

## 11. Developer working agreement

1. Work milestone by milestone; do not implement the whole product in one change.
2. Before each milestone, inspect the repository and summarize the narrow implementation plan.
3. Add tests alongside business logic; test external APIs with mocks.
4. Run pytest, Ruff, Django system checks, and applicable security/dependency checks. Fix failures instead of weakening tests.
5. For Gemini or LinkedIn changes, inspect current official API docs and record relevant API versions, permissions, and limitations in the implementation notes.
6. Do not make real API calls, publish content, or change connected accounts without explicit approval for that manual verification step.
7. At each milestone's completion, report files changed, tests run, known limitations, exact manual verification steps, and wait for owner review before moving on.
8. Never commit credentials, and do not create commits unless requested by the owner.

## 12. Decisions to confirm before coding

This roadmap assumes:

- The Python codebase is a fresh implementation and the Rails repository is reference material only.
- V1 is a single-owner application for a user.
- Django 5.2 LTS is selected for its support window; verify current supported patch and package compatibility at kickoff.
- The owner will apply for the required LinkedIn API product access and member-posting permission; API access is not guaranteed solely by implementing OAuth.
- The owner will provide any additional verified founder/company facts and authentic writing samples.

If any assumption is wrong, update this brief before the affected milestone begins.

## 13. Official references

Recheck documentation when implementing the corresponding milestone; API versions and package support can change.

- [Django supported versions and downloads](https://www.djangoproject.com/download/)
- [Django 5.2 release notes and supported Python versions](https://docs.djangoproject.com/en/5.2/releases/5.2/)
- [Celery: first steps with Django](https://docs.celeryq.dev/en/stable/django/first-steps-with-django.html)
- [Gemini API reference](https://ai.google.dev/gemini-api/docs)
- [Gemini structured output guide](https://ai.google.dev/gemini-api/docs/structured-output?lang=python)
- [LinkedIn Authorization Code Flow](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow)
- [LinkedIn Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api)
- [LinkedIn token refresh](https://learn.microsoft.com/en-us/linkedin/shared/authentication/programmatic-refresh-tokens)
