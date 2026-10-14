from unittest import mock

import pytest
from django.urls import reverse


def test_liveness_returns_ok(client):
    response = client.get(reverse("core:health"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_liveness_rejects_post(client):
    assert client.post(reverse("core:health")).status_code == 405


@pytest.mark.django_db
def test_readiness_ok_when_database_and_redis_reachable(client):
    with mock.patch("apps.core.health.redis_ok", return_value=True):
        response = client.get(reverse("core:health-ready"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


@pytest.mark.django_db
def test_readiness_503_when_redis_down_and_hides_error_details(client):
    with mock.patch("redis.Redis.ping", side_effect=ConnectionError("redis://secret@host")):
        response = client.get(reverse("core:health-ready"))

    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "unavailable"
    assert b"secret" not in response.content


def test_readiness_503_when_database_down(client):
    with (
        mock.patch("apps.core.health.database_ok", return_value=False),
        mock.patch("apps.core.health.redis_ok", return_value=True),
    ):
        response = client.get(reverse("core:health-ready"))

    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "unavailable", "redis": "ok"}
