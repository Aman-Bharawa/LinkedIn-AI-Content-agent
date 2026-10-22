# Deployment and recovery runbook

## Runtime services

Production runs three processes from the same image: Django web (Gunicorn), one Celery worker,
and one Celery Beat scheduler. Use managed PostgreSQL and Redis with TLS and private networking.
The local Compose file includes PostgreSQL and Redis for development; do not expose those ports
to the public internet in a production deployment. Put the web process behind an HTTPS reverse
proxy and provide its public host in `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS`.

Configure the following values through the hosting provider's secret manager, not the image:

- `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false`, `DJANGO_SETTINGS_MODULE=config.settings.prod`
- `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_SSLMODE=require`
- `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`
- `TOKEN_ENCRYPTION_KEYS` (Fernet key; preserve old keys during rotation)
- `GEMINI_API_KEY`, `GEMINI_MODEL`
- `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, `LINKEDIN_REDIRECT_URI`,
  `LINKEDIN_API_VERSION=202609`

Set a permanent HTTPS callback URI in the LinkedIn Developer Portal and use the identical value
in `LINKEDIN_REDIRECT_URI`. Enable the OpenID Connect sign-in product and the product that grants
`w_member_social`. LinkedIn must approve the app's access before OAuth or publishing can be verified.
Never pass the client secret or access token to a user-facing page or log.

## Release

1. Build the Docker image from the repository root and deploy it to the web, worker, and Beat
   services. Run only one Beat scheduler instance.
2. Before routing traffic, run `python manage.py migrate --noinput` once as a release job.
3. Run `python manage.py collectstatic --noinput` during image/release preparation.
4. Run `python manage.py check --deploy` with production settings and actual deployment values.
5. Create the owner with `python manage.py createsuperuser`, then sign in and connect LinkedIn.
6. Verify `/health/` and `/health/ready/`, owner login, a generated draft, and a worker task. Do
   not use a real publish action as a deployment smoke test; verify publishing with a mock first.
7. Confirm approval requires a future publication time and the detail page offers reschedule/cancel
   before publishing starts. The 08:00 Beat task queues two drafts from the owner's topic pool for
   review only. On Windows, the same generation can run locally without Beat or Redis using
   `DJANGO_SETTINGS_MODULE=config.settings.prod python manage.py generate_morning_drafts`; see the
   README for the Task Scheduler setup and network requirements.

The Compose `web` command runs migrations for the single-instance local stack. For production or
multiple web replicas, use the one-off release migration above and override the Compose web
command so replicas do not race to migrate.

## Backups and recovery

Enable automated encrypted point-in-time recovery on managed PostgreSQL and keep backups in a
separate account or region. Back up the Fernet key set separately in the hosting secret manager:
database backups containing LinkedIn tokens cannot be decrypted without the matching key. Restrict
backup access to the project owner and the recovery operator.

For a manual snapshot, run from a trusted operator shell with the managed database credentials
already supplied in its environment:

```powershell
pg_dump --format=custom --no-owner --file=linkedin-agent-YYYYMMDD.dump $env:DB_NAME
```

To rehearse recovery, restore into a new isolated database, set `DB_NAME` to that database, set
`TOKEN_ENCRYPTION_KEYS` to the retained key list, then run:

```powershell
pg_restore --no-owner --dbname=$env:DB_NAME linkedin-agent-YYYYMMDD.dump
python manage.py check
python manage.py showmigrations
```

Check that the owner can sign in, account tokens decrypt, audit/history rows are present, and
health/readiness checks succeed before considering the rehearsal complete. Keep the production
database untouched during a restore rehearsal. Record the rehearsal date and observed restore time
in the operator's incident log.

## Token key rotation

1. Generate a new Fernet key and add it before old keys in `TOKEN_ENCRYPTION_KEYS`.
2. Deploy the new list to all web and worker processes.
3. Run `python manage.py rotate_token_keys` once.
4. Verify account token decryption, then remove retired keys from the secret manager and redeploy.
5. Preserve the previous secret version until the backup retention window that depends on it ends.

## Operational limits

- Access tokens are checked for expiry. The owner must reconnect when they expire; automated refresh
  is intentionally not assumed because programmatic refresh tokens are limited to approved LinkedIn
  partners.
- A network timeout during publication is marked `unknown` / `needs manual review`. Check LinkedIn
  before taking any further action; the system will not blindly retry.
- Scheduled publishing requires Celery Beat and a worker to be running when the selected time arrives.
  If either process is stopped, due posts remain queued and are processed after the processes resume.
- `manage.py check --deploy` and a backup restore rehearsal must be run in the target hosting
  environment before launch. They cannot be certified from a local checkout alone.
