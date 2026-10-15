from __future__ import annotations

import re
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.security import redact_secrets


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# Metadata keys that look like they could carry credentials are refused outright.
_SECRET_KEY_PATTERN = re.compile(
    r"token|secret|password|authorization|bearer|api[_-]?key|cookie", re.IGNORECASE
)


def _find_secret_keys(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if _SECRET_KEY_PATTERN.search(str(key)):
                found.append(child_path)
            found.extend(_find_secret_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_find_secret_keys(child, f"{path}[{index}]"))
    return found


class AuditAction(models.TextChoices):
    STATUS_CHANGE = "status_change", "Status change"
    EDIT = "edit", "Edit"
    REGENERATE = "regenerate", "Regenerate"
    REJECT = "reject", "Reject"
    APPROVE = "approve", "Approve"
    RETRY = "retry", "Retry"
    CONNECT = "connect", "Connect LinkedIn"
    DISCONNECT = "disconnect", "Disconnect LinkedIn"


class ScheduledDraftRun(models.Model):
    """One idempotent weekday auto-draft run per owner-local calendar date."""

    class Status(models.TextChoices):
        STARTED = "started", "Started"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    run_date = models.DateField(unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.STARTED)
    post = models.ForeignKey("posts.LinkedInPost", null=True, blank=True, on_delete=models.SET_NULL)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-run_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["started", "succeeded", "failed"]),
                name="scheduled_run_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"Auto-draft run {self.run_date} ({self.status})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.error_message = redact_secrets(self.error_message)
        super().save(*args, **kwargs)


class AuditEvent(models.Model):
    """Append-only record of owner actions. Never stores secrets."""

    Action = AuditAction

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    action = models.CharField(max_length=32, choices=AuditAction.choices)
    post = models.ForeignKey(
        "posts.LinkedInPost",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    from_status = models.CharField(max_length=32, blank=True)
    to_status = models.CharField(max_length=32, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action", "-created_at"], name="audit_action_created_idx"),
            models.Index(fields=["post", "-created_at"], name="audit_post_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=AuditAction.values),
                name="audit_action_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_action_display()} at {self.created_at:%Y-%m-%d %H:%M}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None and not self._state.adding:
            raise ValidationError("Audit events are append-only.")
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise ValidationError("Audit events are append-only.")

    def clean(self) -> None:
        if not isinstance(self.metadata, dict):
            raise ValidationError({"metadata": "Metadata must be an object."})
        secret_keys = _find_secret_keys(self.metadata)
        if secret_keys:
            raise ValidationError(
                {"metadata": f"Metadata must not contain secrets: {', '.join(secret_keys)}"}
            )
