import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("linkedin_agent")
# All Celery settings live in Django settings under the CELERY_ prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
app.conf.beat_schedule = {
    "morning-owner-auto-draft": {
        "task": "posts.create_weekday_auto_draft",
        "schedule": crontab(minute=0, hour=8),
    },
    "recover-stale-work": {
        "task": "posts.recover_stale_work",
        "schedule": crontab(minute="*/5"),
    },
    "enqueue-due-linkedin-publications": {
        "task": "linkedin.enqueue_due_publications",
        "schedule": crontab(minute="*/1"),
    },
}


@app.task(name="core.ping")
def ping() -> str:
    """Trivial task used to verify that a worker is consuming the queue."""
    return "pong"
