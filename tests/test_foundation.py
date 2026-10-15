import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.urls import reverse

from config.celery import app, ping


def test_owner_timezone_and_utc_storage():
    assert settings.TIME_ZONE == "Asia/Kolkata"
    assert settings.USE_TZ is True
    assert settings.CELERY_TIMEZONE == "Asia/Kolkata"


def test_celery_is_configured_from_django_settings():
    assert app.conf.task_serializer == "json"
    assert app.conf.accept_content == ["json"]
    assert app.conf.task_acks_late is True


def test_celery_task_runs():
    assert ping.delay().get(timeout=5) == "pong"


def test_home_page_requires_login(client):
    response = client.get(reverse("core:home"))

    assert response.status_code == 302
    assert response.url.startswith("/django-admin/login/")


@pytest.mark.django_db
def test_home_page_renders_with_bootstrap_for_owner(client, django_user_model):
    owner = django_user_model.objects.create_superuser(
        username="owner", email="owner@example.com", password="unused-in-test"
    )
    client.force_login(owner)

    response = client.get(reverse("core:home"))

    assert response.status_code == 200
    html = response.content.decode()
    assert "vendor/bootstrap/css/bootstrap.min.css" in html
    assert "vendor/bootstrap/js/bootstrap.bundle.min.js" in html
    assert "vendor/htmx/htmx.min.js" in html


def test_vendored_static_files_exist():
    for path in (
        "vendor/bootstrap/css/bootstrap.min.css",
        "vendor/bootstrap/js/bootstrap.bundle.min.js",
        "vendor/htmx/htmx.min.js",
    ):
        assert finders.find(path), path


def test_django_admin_moved_off_admin_prefix(client):
    assert reverse("admin:index") == "/django-admin/"
