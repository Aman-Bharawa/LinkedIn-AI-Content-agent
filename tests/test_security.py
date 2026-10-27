"""Token encryption, key rotation, masking, redaction and token exposure (brief §5, §7.6, §8)."""

import io
import logging
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import IntegrityError, connection
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from redis.exceptions import RedisError

from apps.accounts.models import LinkedInAccount, is_owner
from apps.core.middleware import OwnerActionRateLimitMiddleware
from apps.core.models import AuditEvent
from apps.core.security import (
    REDACTED,
    SecretValue,
    TokenDecryptionError,
    decrypt,
    encrypt,
    redact_secrets,
    token_encryption_keys_check,
)

from .conftest import ACCESS_TOKEN, REFRESH_TOKEN


def raw_column(account, column):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {column} FROM accounts_linkedinaccount WHERE id = %s", [account.pk])  # noqa: S608
        return cursor.fetchone()[0]


def test_encrypt_round_trip_and_ciphertext_differs():
    ciphertext = encrypt("secret-value")
    assert ciphertext != "secret-value"
    assert decrypt(ciphertext) == "secret-value"


def test_decrypt_with_wrong_key_raises_generic_error(settings):
    ciphertext = encrypt("secret-value")
    settings.TOKEN_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
    with pytest.raises(TokenDecryptionError) as exc:
        decrypt(ciphertext)
    assert "secret-value" not in str(exc.value)


def test_missing_keys_is_improperly_configured(settings):
    settings.TOKEN_ENCRYPTION_KEYS = []
    with pytest.raises(ImproperlyConfigured):
        encrypt("x")


def test_tokens_are_encrypted_at_rest(account):
    stored_access = raw_column(account, "access_token")
    stored_refresh = raw_column(account, "refresh_token")
    assert ACCESS_TOKEN not in stored_access
    assert REFRESH_TOKEN not in stored_refresh

    fresh = LinkedInAccount.objects.get(pk=account.pk)
    assert isinstance(fresh.access_token, SecretValue)
    assert fresh.access_token.reveal() == ACCESS_TOKEN
    assert fresh.refresh_token.reveal() == REFRESH_TOKEN


def test_null_refresh_token_stays_null(db):
    account = LinkedInAccount.objects.create(
        member_urn="urn:li:person:norefresh",
        access_token=ACCESS_TOKEN,
        access_token_expires_at=timezone.now() + timedelta(days=1),
    )
    assert raw_column(account, "refresh_token") is None
    assert LinkedInAccount.objects.get(pk=account.pk).refresh_token is None


def test_key_rotation_reencrypts_with_new_key(settings, account):
    old_key = settings.TOKEN_ENCRYPTION_KEYS[0]
    new_key = Fernet.generate_key().decode()

    settings.TOKEN_ENCRYPTION_KEYS = [new_key, old_key]
    assert LinkedInAccount.objects.get(pk=account.pk).access_token.reveal() == ACCESS_TOKEN
    call_command("rotate_token_keys", stdout=io.StringIO())

    settings.TOKEN_ENCRYPTION_KEYS = [new_key]
    fresh = LinkedInAccount.objects.get(pk=account.pk)
    assert fresh.access_token.reveal() == ACCESS_TOKEN
    assert fresh.refresh_token.reveal() == REFRESH_TOKEN


def test_system_check_flags_missing_invalid_and_reused_keys(settings):
    settings.TOKEN_ENCRYPTION_KEYS = []
    assert [m.id for m in token_encryption_keys_check()] == ["core.W001"]

    settings.TOKEN_ENCRYPTION_KEYS = ["not-a-key"]
    assert "core.E001" in [m.id for m in token_encryption_keys_check()]

    settings.TOKEN_ENCRYPTION_KEYS = [settings.SECRET_KEY]
    assert "core.E002" in [m.id for m in token_encryption_keys_check()]


def test_secret_value_is_masked_everywhere(account, caplog):
    token = LinkedInAccount.objects.get(pk=account.pk).access_token
    assert ACCESS_TOKEN not in str(token)
    assert ACCESS_TOKEN not in repr(token)
    assert ACCESS_TOKEN not in f"{token}"
    with caplog.at_level(logging.INFO):
        logging.getLogger("test").info("token=%s account=%r", token, account)
    assert ACCESS_TOKEN not in caplog.text
    assert ACCESS_TOKEN not in repr(account)


def test_admin_pages_never_show_tokens(client, owner, account):
    client.force_login(owner)
    for url in (
        reverse("admin:accounts_linkedinaccount_changelist"),
        reverse("admin:accounts_linkedinaccount_change", args=[account.pk]),
    ):
        response = client.get(url)
        assert response.status_code == 200
        html = response.content.decode()
        assert ACCESS_TOKEN not in html
        assert REFRESH_TOKEN not in html
        assert raw_column(account, "access_token") not in html


def test_admin_cannot_add_or_edit_accounts(client, owner, account):
    client.force_login(owner)
    assert client.get(reverse("admin:accounts_linkedinaccount_add")).status_code == 403
    response = client.post(
        reverse("admin:accounts_linkedinaccount_change", args=[account.pk]),
        {"member_urn": "urn:li:person:hijack"},
    )
    assert response.status_code == 403


def test_staff_non_superuser_cannot_access_global_admin(client, staff_user):
    client.force_login(staff_user)
    response = client.get(reverse("admin:index"))
    assert response.status_code == 404


def test_login_rate_limit_is_shared_by_username_and_client_ip(client, db):
    cache.clear()
    url = reverse("accounts:login")
    for _ in range(5):
        response = client.post(
            url,
            {"username": "unknown-user", "password": "wrong-password"},
            HTTP_X_FORWARDED_FOR="203.0.113.8, 10.0.0.2",
        )
        assert response.status_code == 200
    response = client.post(
        url,
        {"username": "unknown-user", "password": "wrong-password"},
        HTTP_X_FORWARDED_FOR="203.0.113.8, 10.0.0.2",
    )
    assert response.status_code == 429
    assert response["Retry-After"] == "300"


def test_admin_login_rate_limit_is_applied(client, db):
    cache.clear()
    url = reverse("admin:login")
    for _ in range(5):
        response = client.post(
            url,
            {"username": "unknown-admin", "password": "wrong-password"},
            HTTP_X_FORWARDED_FOR="203.0.113.9",
        )
        assert response.status_code == 200
    response = client.post(
        url,
        {"username": "unknown-admin", "password": "wrong-password"},
        HTTP_X_FORWARDED_FOR="203.0.113.9",
    )
    assert response.status_code == 429


def test_post_regeneration_is_rate_limited():
    cache.clear()
    middleware = OwnerActionRateLimitMiddleware(lambda request: HttpResponse(status=200))
    factory = RequestFactory()
    for _ in range(10):
        response = middleware(
            factory.post("/posts/42/regenerate/", HTTP_X_FORWARDED_FOR="203.0.113.10")
        )
        assert response.status_code == 200
    response = middleware(
        factory.post("/posts/42/regenerate/", HTTP_X_FORWARDED_FOR="203.0.113.10")
    )
    assert response.status_code == 429


def test_login_rate_limit_fails_closed_when_shared_cache_is_unavailable(client, monkeypatch):
    def fail_cache(*args, **kwargs):
        raise RedisError("cache unavailable")

    monkeypatch.setattr(cache, "add", fail_cache)
    response = client.post(
        reverse("accounts:login"),
        {"username": "unknown-user", "password": "wrong-password"},
    )
    assert response.status_code == 503


def test_security_headers_are_set(client):
    response = client.get(reverse("accounts:login"))
    assert response["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"
    assert response["Cross-Origin-Resource-Policy"] == "same-origin"
    assert response["X-Permitted-Cross-Domain-Policies"] == "none"
    policy = response["Content-Security-Policy"]
    assert "script-src 'self'" in policy
    assert "unsafe-inline" not in policy.split("script-src", 1)[1].split(";", 1)[0]


def test_signup_help_text_is_rendered_as_text(client):
    response = client.get(reverse("accounts:sign-up"))
    assert b"&lt;ul" not in response.content
    assert b"Use at least 12 characters." in response.content


def test_invalid_signup_input_is_escaped_in_response(client, db):
    response = client.post(
        reverse("accounts:sign-up"),
        {
            "username": "<script>alert(1)</script>",
            "email": "test@example.com",
            "password1": "unused-valid-password-123",
            "password2": "unused-valid-password-123",
        },
    )
    assert response.status_code == 200
    assert b"<script>alert(1)</script>" not in response.content


def test_dumpdata_masks_tokens(account, capsys):
    call_command("dumpdata", "accounts.linkedinaccount")
    output = capsys.readouterr().out
    assert ACCESS_TOKEN not in output
    assert SecretValue.MASK in output


def test_only_one_active_linkedin_account_per_owner(account):
    with pytest.raises(IntegrityError):
        LinkedInAccount.objects.create(
            owner=account.owner,
            member_urn="urn:li:person:second",
            access_token="x",
            access_token_expires_at=timezone.now(),
        )


def test_inactive_accounts_may_coexist(account):
    account.is_active = False
    account.save()
    LinkedInAccount.objects.create(
        owner=account.owner,
        member_urn="urn:li:person:second",
        access_token="x",
        access_token_expires_at=timezone.now(),
    )
    assert LinkedInAccount.objects.count() == 2


def test_member_urn_format_enforced(db):
    with pytest.raises(IntegrityError):
        LinkedInAccount.objects.create(
            member_urn="abc", access_token="x", access_token_expires_at=timezone.now()
        )


def test_token_expiry_helper(account):
    assert not account.is_access_token_expired()
    assert account.is_access_token_expired(now=account.access_token_expires_at)
    assert account.has_scope("w_member_social")


def test_is_owner_accepts_any_active_authenticated_user(owner, staff_user):
    assert is_owner(owner)
    assert is_owner(staff_user)
    assert not is_owner(None)
    owner.is_active = False
    assert not is_owner(owner)


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer AQXsecret123",
        "headers={'Authorization': 'Bearer AQXsecret123'}",
        '{"access_token": "AQXsecret123", "expires_in": 5184000}',
        "refresh_token=AQXsecret123&client_secret=AQXsecret123",
        "openai key sk-AQXsecret123456",
    ],
)
def test_redact_secrets_removes_credentials(text):
    result = redact_secrets(text)
    assert "AQXsecret123" not in result
    assert REDACTED in result


def test_redact_secrets_truncates():
    assert len(redact_secrets("x" * 5000, max_length=100)) == 100


def test_audit_event_rejects_secret_metadata(owner):
    with pytest.raises(Exception, match="must not contain secrets"):
        AuditEvent.objects.create(
            actor=owner, action="connect", metadata={"response": {"access_token": "x"}}
        )


def test_audit_events_are_append_only(owner):
    event = AuditEvent.objects.create(actor=owner, action="connect", metadata={"scopes": 2})
    with pytest.raises(Exception, match="append-only"):
        event.save()
    with pytest.raises(Exception, match="append-only"):
        event.delete()
