from __future__ import annotations

from datetime import datetime
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel
from apps.core.security import EncryptedTextField


def is_owner(user: Any) -> bool:
    """Whether this active signed-in user may use their private workspace."""
    return bool(user is not None and getattr(user, "is_authenticated", False) and user.is_active)


class LinkedInAccount(TimeStampedModel):
    """A user's connected LinkedIn member account.

    Tokens are encrypted at rest (see apps.core.security) and surface in Python only
    as masked ``SecretValue`` objects. They are excluded from admin, forms and
    string representations.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="linkedin_accounts",
    )
    member_urn = models.CharField(
        max_length=255,
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
            models.UniqueConstraint(
                fields=["owner"],
                condition=models.Q(is_active=True),
                name="linkedin_account_owner_active_uniq",
            ),
            models.UniqueConstraint(
                fields=["owner", "member_urn"], name="linkedin_account_owner_member_uniq"
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
