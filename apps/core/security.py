"""Secret handling: token encryption, the encrypted model field, redaction, config checks.

Encryption uses Fernet (AES-128-CBC + HMAC-SHA256, i.e. authenticated) via MultiFernet:
* ``TOKEN_ENCRYPTION_KEYS`` lists Fernet keys, newest first; it is never Django's SECRET_KEY.
* New values are encrypted with the first key; any listed key can decrypt.
* ``manage.py rotate_token_keys`` re-encrypts stored tokens with the first key
  (rotation steps are in the README).
"""

from __future__ import annotations

import hmac
import re
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.checks import Error, Tags, register
from django.core.checks import Warning as CheckWarning
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.db import models


class SecretValue:
    """A decrypted secret that refuses to print itself.

    str()/repr() are masked so the value cannot leak through templates, logs,
    exception messages or debug pages. Call ``reveal()`` only where the raw value
    is sent to LinkedIn.
    """

    __slots__ = ("_value",)
    MASK = "********"

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("SecretValue wraps a str")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __str__(self) -> str:
        return self.MASK

    def __repr__(self) -> str:
        return f"SecretValue({self.MASK!r})"

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SecretValue):
            return hmac.compare_digest(self._value, other._value)
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]


class TokenDecryptionError(Exception):
    """Stored ciphertext could not be decrypted. Never includes the value."""

    def __init__(self) -> None:
        super().__init__(
            "Stored secret could not be decrypted with the configured TOKEN_ENCRYPTION_KEYS."
        )


def _configured_keys() -> tuple[str, ...]:
    return tuple(key for key in getattr(settings, "TOKEN_ENCRYPTION_KEYS", []) or [] if key)


@lru_cache(maxsize=1)
def _fernet(keys: tuple[str, ...]) -> MultiFernet:
    if not keys:
        raise ImproperlyConfigured(
            "TOKEN_ENCRYPTION_KEYS is not set. Generate a key with "
            '`uv run python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"` and add it to .env.'
        )
    try:
        return MultiFernet([Fernet(key.encode()) for key in keys])
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured("TOKEN_ENCRYPTION_KEYS contains an invalid Fernet key.") from exc


def encrypt(plaintext: str) -> str:
    return _fernet(_configured_keys()).encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet(_configured_keys()).decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise TokenDecryptionError() from None


def _clear_cache(*, setting: str, **kwargs: object) -> None:
    if setting == "TOKEN_ENCRYPTION_KEYS":
        _fernet.cache_clear()


setting_changed.connect(_clear_cache)


class EncryptedTextField(models.TextField):
    """Stores a secret encrypted at rest; exposes it in Python as a masked SecretValue.

    Querying by value is not supported (ciphertext is non-deterministic), and
    ``dumpdata`` output is masked; back up with ``pg_dump``.
    """

    description = "Text encrypted with TOKEN_ENCRYPTION_KEYS"

    def from_db_value(self, value: str | None, expression: Any, connection: Any) -> Any:
        if value is None:
            return None
        return SecretValue(decrypt(value))

    def to_python(self, value: Any) -> Any:
        if value is None or isinstance(value, SecretValue):
            return value
        return SecretValue(str(value))

    def get_prep_value(self, value: Any) -> Any:
        if value is None:
            return None
        return encrypt(self.to_python(value).reveal())

    def value_to_string(self, obj: models.Model) -> str:
        return SecretValue.MASK


REDACTED = "[REDACTED]"

_REDACTION_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;\"']+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*"),
    re.compile(
        r"(?i)([\"']?(?:access_token|refresh_token|client_secret|id_token|api_key|password)"
        r"[\"']?\s*[:=]\s*[\"']?)[^\s,;\"'&}]+"
    ),
    re.compile(r"()\bsk-[A-Za-z0-9_\-]{8,}"),
]


def redact_secrets(text: str, max_length: int = 1000) -> str:
    """Scrub credentials from text before it is stored or shown."""
    if not text:
        return ""
    for pattern in _REDACTION_PATTERNS:
        text = pattern.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    if len(text) > max_length:
        text = text[: max_length - 1] + "…"
    return text


@register(Tags.security)
def token_encryption_keys_check(app_configs: Any = None, **kwargs: Any) -> list[Any]:
    keys = _configured_keys()
    if not keys:
        return [
            CheckWarning(
                "TOKEN_ENCRYPTION_KEYS is empty; LinkedIn tokens cannot be stored.",
                hint="Generate a Fernet key and add it to .env (see .env.example).",
                id="core.W001",
            )
        ]
    errors: list[Any] = []
    for index, key in enumerate(keys, start=1):
        if key == settings.SECRET_KEY:
            errors.append(
                Error("TOKEN_ENCRYPTION_KEYS must not reuse DJANGO_SECRET_KEY.", id="core.E002")
            )
        try:
            Fernet(key.encode())
        except (ValueError, TypeError):
            errors.append(
                Error(
                    f"TOKEN_ENCRYPTION_KEYS entry #{index} is not a valid Fernet key.",
                    id="core.E001",
                )
            )
    return errors
