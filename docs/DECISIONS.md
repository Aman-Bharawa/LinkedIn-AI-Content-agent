# Implementation decisions

The source of truth is [IMPLEMENTATION_BRIEF.md](IMPLEMENTATION_BRIEF.md). This file records every
place where the implementation interprets, extends, or differs from the brief, and the owner
decisions taken at kickoff (brief section 12).

## Owner decisions (kickoff, 2026-10-14)

| Topic | Decision |
|---|---|
| Codebase | Fresh Python implementation. There is no Rails prototype or data to migrate. |
| Users | Single owner (brief section 2). |
| Owner timezone | `Asia/Kolkata`. Times are stored in UTC and shown/scheduled in IST. |
| Workflow (brief amended 2026-10-14) | Owner keyword → AI post → approval queue → approve or reject (reject removes from the queue). Weekday 08:00 auto-draft job kept. Posts tagged **Keyword** or **Auto draft**. **Publishing stays manual** as in the original brief: no scheduled job publishes. See the amendments at the top of the brief. |
| UI library | Bootstrap 5.3 (CSS + JS) with Django templates and HTMX, at the owner's request. |
| Python environment | Project-local `.venv` managed by `uv`; versions pinned in `uv.lock`. |
| Verified facts / writing samples | Not supplied yet. `founder.md` and `company.md` ship with no facts; `writing_examples/` is empty. The owner adds them later. |
| Weekday auto-drafts (2026-10-16) | At 08:00 Asia/Kolkata on weekdays, queue three distinct-topic drafts for manual review. They are never approved or published automatically. |
| Post format | Owner asked (M2) for the standard LinkedIn post format. Written into `writing_style.md` as rules and post structures, not as sample posts, because the brief forbids seeding generated posts as genuine examples. |
| LinkedIn product access | Still to be confirmed by the owner before Milestone 9. |

## Deviations and interpretations

| Brief says | Implementation | Reason |
|---|---|---|
| §3 Local environment: Docker Compose | Compose file provided at `docker/docker-compose.yml` (PostgreSQL 18, Redis 7). The owner's Windows machine runs PostgreSQL 18 and Memurai natively. | Docker is not installed on the owner's machine; native services satisfy the same acceptance criteria. |
| §3 Redis 7 | CI and Compose use Redis 7. Locally, Memurai Developer 8.2 (Redis 8.2 compatible). | Redis has no official Windows build; Memurai is wire-compatible and newer. |
| §3 Layout | Adds `apps/core/` (health endpoints, base page) and `docs/`. Domain apps from the suggested layout are created in the milestone that needs them. | Health checks are not domain logic; brief allows a documented layout. |
| §3 Dependency tooling (unspecified) | `uv` with `uv.lock` | Exact pins and reproducible installs, also manages Python 3.13. |
| M7 dashboard at `/admin/posts` | Django's built-in admin is mounted at `/django-admin/` | Keeps `/admin/` free for the owner dashboard required by M7. |
| §8 Owner-only pages | Every non-health page uses `login_required`. Until M7 adds the owner login page, `LOGIN_URL` points to Django admin's login. | No public pages in V1. Health endpoints stay public and expose only ok/unavailable. |
| M0 Health endpoint | `/health/` (liveness, no dependencies) and `/health/ready/` (PostgreSQL and Redis). | Readiness is needed again in M13; error details are never returned. |
| Front-end assets | Bootstrap 5.3.8 and HTMX 2.0.7 vendored under `static/vendor/` with SHA-256s in `VENDOR.md`. | No runtime CDN dependency; allows a strict Content Security Policy in M13. |
| Celery worker on Windows | Run with `--pool=solo`. | Celery's default prefork pool is not supported on Windows. Production (Linux containers) uses the default. |
| §5 "topic and content pillar" (M1) | `ContentPillar` table created in M1; posts and topics reference it by foreign key. M3 adds groups, weights and the 13 initial pillars. | Real foreign keys keep pillar data consistent; avoids a free-text pillar that M3 would have to migrate. |
| §5 LinkedInPost topic | `topic` (editable text) plus optional `content_topic` link to `ContentTopic`. | Owner can edit the topic text (M8) while selection history stays linked. |
| §5 Token encryption | Fernet via `MultiFernet` (`apps/core/security.py`); tokens surface as masked `SecretValue`; `rotate_token_keys` command. | Authenticated encryption with key rotation, as required; masking blocks accidental leaks in logs/templates. |
| §5 WritingExample | Adds `is_owner_approved`; only active + owner-approved examples are usable. | Enforces "only authentic, founder-approved examples" in data, not just convention. |
| §5 ContentKnowledge | Verified knowledge must have a `source` (database constraint) and records `verified_at`. | Provenance is mandatory for anything used as fact. |
| §5 PostReview scores | 0–10 integers. Quality: authenticity, usefulness, originality, founder voice, clarity, hook quality, overall (higher = better). Risk: AI-like, promotional, repetition (higher = worse). Thresholds come in M6. | Brief requires explicit direction; hook quality and repetition come from the M6 review criteria. |
| §5 Owner approval | Owner = active superuser. Database constraint: approved/publishing/published posts must have `approved_by` + `approved_at`. Approval is cleared if the post leaves the approval path (e.g. publishing fails). | AI approval can never substitute for owner approval, enforced in both code and database. |
| §5 State machine | `apps/posts/state.py` `transition_post()` with row locking. Only `approved → publishing`; `publishing` timeouts go to `needs_manual_review`, never back to retry. | Brief §5, §7 and M10 safety rules. |
| M6 "maximum three attempts" | Database constraint `generation_attempts <= 3` on posts and `attempt_number` 1–3 on reviews. | The retry limit can't be bypassed by a bug. |
| §5 PostPublication | Partial unique constraints: one active attempt (pending/in progress/unknown) and one success per post; unique `idempotency_key`; error text redacted on save. | One active attempt per post, idempotent operations, safe error details. |
| §5 AuditEvent | Lives in `apps/core`; append-only; rejects metadata keys that look like secrets. | Owner actions span accounts and posts. |
| M2 knowledge source | Markdown files are the version-controlled source; `sync_knowledge` imports them into `ContentKnowledge` / `WritingExample`; AI services read only the database through `load_knowledge_context()`. | One runtime source; admin edits and file edits both work; verification is enforced by the database. |
| M2 verification rule | A fact is verified only if its bullet ends with `(source: ...)`; otherwise it is stored unverified with file provenance and never used. Removed facts are deactivated, not deleted. | "Unsupported claims are never elevated into verified knowledge"; keeps an audit trail. |
| M2 approved topics | The 13 approved subject areas (brief §4) live in `writing_style.md` under "Approved subject areas" and load as `subject_area` knowledge. | Avoids an extra file; pillars and weights remain M3. |
| M2 post formats | `writing_style.md` defines `lesson`, `how_to`, `contrarian`, `framework`, `myth_vs_reality`, `question`. | Gives M4's `format` field a fixed, owner-editable vocabulary. |
| M3 strategy weights | Five editable `ContentGroup` records hold target percentages; the 13 initial pillars map to one group each and divide that group's target equally across active pillars. | Implements the brief's fallback weighting rule while letting group weights and active pillars change without editing AI services. |
| M3 weight validation | `validate_content_strategy()` requires active groups to total 100%, each active group to contain an active pillar, and active pillars to map to active groups. Per-pillar shares are allocated to hundredths, with any remainder assigned by pillar name. | Makes allocations sum exactly to 100% despite groups with repeating fractional shares. |
| Friday demo scope | For the owner-approved Friday demo, generation is synchronous from the owner dashboard. Generated posts enter `needs_manual_review`; AI quality scoring and scheduled drafting remain later work. The owner can edit (versioned), approve, or reject. Publishing is unavailable in the demo. | Delivers the demonstrated human-review loop without implying AI scoring or LinkedIn publishing exists. |
| M4-M5 provider integration | Use Google's official `google-genai` Python SDK and Gemini Interactions API with JSON Schema structured output. The model and API key come from environment configuration; requests set `store=False`. | Supports structured generation on Gemini's free tier, keeps model configurable, and keeps credentials out of source control. |
| M5 topic-to-post boundary | Topic selection persists the topic, pillar, angle, and format first. Post generation receives that saved selection plus verified knowledge, approved examples, and recent posts; it returns only hook, body, and hashtags, so it cannot silently change the selected topic or pillar. | Keeps strategy and duplicate checks authoritative and preserves an auditable relationship between the generated copy and its topic. |
| M6 quality gates | Quality scores pass at 6/10 or higher; AI-like risk must be 4/10 or lower; promotional and repetition risks must be 5/10 or lower. Unsupported claims always fail. A passing AI review still routes to owner review and never approves or publishes. | Makes score direction and retry behavior explicit, configurable, and safe by default. |
| CI trigger | Runs on branch pushes and pull requests, not tag pushes. | Avoids duplicate runs when a milestone tag is pushed. |
