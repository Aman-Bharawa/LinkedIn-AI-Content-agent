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
| UI library | Bootstrap 5.3 (CSS + JS) with Django templates and HTMX, at the owner's request. |
| Python environment | Project-local `.venv` managed by `uv`; versions pinned in `uv.lock`. |
| Verified facts / writing samples | Still to be supplied by the owner before Milestone 2. |
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
