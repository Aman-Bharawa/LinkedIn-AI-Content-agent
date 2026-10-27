import hashlib
import json
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.extensions.models import ExtensionAccessToken, ExtensionPairingCode
from apps.posts.models import LinkedInPost, PostStatus

EXTENSION_ORIGIN = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"


@pytest.fixture(autouse=True)
def clear_extension_rate_limits():
    cache.clear()


@pytest.fixture
def extension_token(owner):
    raw_token = "test-extension-access-token"
    row = ExtensionAccessToken.objects.create(
        owner=owner,
        token_digest=hashlib.sha256(raw_token.encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(days=1),
    )
    return raw_token, row


def auth_headers(raw_token):
    return {
        "HTTP_ORIGIN": EXTENSION_ORIGIN,
        "HTTP_AUTHORIZATION": f"Bearer {raw_token}",
    }


@pytest.mark.django_db
def test_pair_page_creates_a_short_lived_code(client, owner):
    client.force_login(owner)

    response = client.get("/extension/pair/")

    assert response.status_code == 200
    code = ExtensionPairingCode.objects.get()
    assert code.expires_at > timezone.now()
    assert code.used_at is None


@pytest.mark.django_db
def test_pairing_code_exchange_is_one_time(client, owner):
    raw_code = "one-time-pairing-code"
    pairing = ExtensionPairingCode.objects.create(
        owner=owner,
        code_digest=hashlib.sha256(raw_code.encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(minutes=5),
    )

    response = client.post(
        "/extension/api/token/",
        data=json.dumps({"code": raw_code}),
        content_type="application/json",
        HTTP_ORIGIN=EXTENSION_ORIGIN,
    )

    assert response.status_code == 200
    token = response.json()["token"]
    stored = ExtensionAccessToken.objects.get(owner=owner)
    assert stored.token_digest == hashlib.sha256(token.encode()).hexdigest()
    pairing.refresh_from_db()
    assert pairing.used_at is not None

    replay = client.post(
        "/extension/api/token/",
        data=json.dumps({"code": raw_code}),
        content_type="application/json",
        HTTP_ORIGIN=EXTENSION_ORIGIN,
    )
    assert replay.status_code == 400


@pytest.mark.django_db
def test_draft_list_requires_origin_and_valid_token(client, extension_token, post):
    raw_token, _ = extension_token

    response = client.post(
        "/extension/api/drafts/list/",
        data="{}",
        content_type="application/json",
        **auth_headers(raw_token),
    )

    assert response.status_code == 200
    assert response["Access-Control-Allow-Origin"] == EXTENSION_ORIGIN
    assert response.json()["drafts"][0]["id"] == post.pk


@pytest.mark.django_db
def test_draft_generation_queues_a_keyword_job(client, monkeypatch, extension_token, owner):
    raw_token, _ = extension_token
    queued = []
    monkeypatch.setattr(
        "apps.posts.tasks.generate_keyword_post.delay",
        lambda post_id, owner_id: queued.append((post_id, owner_id)),
    )

    response = client.post(
        "/extension/api/drafts/",
        data=json.dumps({"keyword": "Building useful habits"}),
        content_type="application/json",
        **auth_headers(raw_token),
    )

    assert response.status_code == 202
    post = LinkedInPost.objects.get(pk=response.json()["id"])
    assert post.owner == owner
    assert post.source_keyword == "Building useful habits"
    assert post.status == PostStatus.GENERATING
    assert queued == [(post.pk, owner.pk)]


@pytest.mark.django_db
def test_approval_approves_and_queues_a_pending_draft(
    client, monkeypatch, extension_token, post
):
    raw_token, _ = extension_token
    monkeypatch.setattr("apps.extensions.views.refresh_publication_queue", lambda **kwargs: None)

    response = client.post(
        f"/extension/api/drafts/{post.pk}/approve/",
        data="{}",
        content_type="application/json",
        **auth_headers(raw_token),
    )

    assert response.status_code == 200
    post.refresh_from_db()
    assert post.status == PostStatus.APPROVED
    assert response.json()["status"] == PostStatus.APPROVED


@pytest.mark.django_db
def test_disconnect_revokes_the_extension_token(client, extension_token):
    raw_token, row = extension_token

    response = client.post(
        "/extension/api/revoke/",
        data="{}",
        content_type="application/json",
        **auth_headers(raw_token),
    )

    assert response.status_code == 200
    row.refresh_from_db()
    assert row.revoked_at is not None


@pytest.mark.django_db
def test_extension_api_rejects_missing_origin(client):
    response = client.post(
        "/extension/api/token/",
        data=json.dumps({"code": "irrelevant"}),
        content_type="application/json",
    )

    assert response.status_code == 403
