# Code review decisions

## Scope

- Keep the existing Django project and app boundaries. Moving modules or merging apps would make this broad cleanup harder to verify and could change import paths used by Celery, migrations, or deployment commands.
- Preserve user-facing workflows, database models, migration history, and external API behavior.
- Keep Django migrations and the deployment, implementation, and product-decision documents because they explain and preserve live application behavior.

## Library choices

- Keep Django for authentication, forms, views, ORM, validation, and admin. These are established framework features already used by the project; replacing them with parallel packages would add complexity.
- Keep Celery and Redis for asynchronous jobs and scheduling. They implement the worker and Beat workflows the application already depends on.
- Keep Google's official `google-genai` SDK for Gemini, `cryptography` for encrypted LinkedIn tokens, `django-environ` for settings, and `psycopg` for PostgreSQL. Each is directly used for its intended integration.
- Remove `django-bootstrap5`: the application uses Bootstrap's static CSS and JavaScript directly and contains no template tags or Python imports from this package.
- Remove HTMX's vendored files and script reference: the templates contain no HTMX attributes or requests. Current interactions use Django forms and the existing page JavaScript.
- Keep Python's standard-library `urllib` for LinkedIn HTTP calls and `difflib.SequenceMatcher` for duplicate-topic checks. Adding a separate HTTP or fuzzy-matching dependency would not improve the current small, focused use cases and could change request or matching behavior.
- Keep the knowledge Markdown parser. It supports the repository's narrow, documented format and provenance rules; replacing it with a general parser would add a dependency and could alter which facts are considered verified.

## Cleanup and verification

- Remove explanatory source comments while retaining interpreter and static-analysis directives that affect execution or checks. Keep docstrings because they are part of Python's API documentation, not comments.
- Format Python consistently without relocating modules or changing database structure.
- Remove only unused frontend assets and libraries. Keep generated caches and Beat state untouched because they may be in active use and can be safely ignored by Git.
- Use a local in-memory SQLite database for developer test runs so tests cannot contact the production database through `.env`. CI continues using its PostgreSQL service.
