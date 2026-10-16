from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel
from apps.core.security import EncryptedTextField


def is_owner(user: Any) -> bool:
    """The single V1 owner is an active superuser. There is no public signup."""
    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and user.is_active
        and user.is_superuser
    )


class LinkedInAccount(TimeStampedModel):
    """The owner's single connected LinkedIn member account.

    Tokens are encrypted at rest (see apps.core.security) and surface in Python only
    as masked ``SecretValue`` objects. They are excluded from admin, forms and
    string representations.
    """

    member_urn = models.CharField(
        max_length=255,
        unique=True,
        help_text="LinkedIn member URN, e.g. urn:li:person:abc123.",
    )
    display_name = models.CharField(max_length=255, blank=True)
    access_token = EncryptedTextField()
    refresh_token = EncryptedTextField(null=True, blank=True)
    access_token_expires_at = models.DateTimeField()
    refresh_token_expires_at = models.DateTimeField(null=True, blank=True)
    scopes = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    connected_at = models.DateTimeField(default=timezone.now)
    disconnected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "LinkedIn account"
        constraints = [
            # Single-owner V1: at most one connected account at a time.
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="linkedin_account_single_active",
            ),
            models.CheckConstraint(
                condition=models.Q(member_urn__startswith="urn:li:"),
                name="linkedin_account_member_urn_format",
            ),
        ]

    def __str__(self) -> str:
        return self.member_urn

    def __repr__(self) -> str:
        return f"<LinkedInAccount id={self.pk} member_urn={self.member_urn!r}>"

    def is_access_token_expired(self, now: datetime | None = None) -> bool:
        return (now or timezone.now()) >= self.access_token_expires_at

    def has_scope(self, scope: str) -> bool:
        return scope in (self.scopes or [])
