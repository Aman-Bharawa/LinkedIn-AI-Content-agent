"""Explicit state machine for LinkedInPost.status (brief §5, §7).

All status changes go through ``transition_post`` which:
* locks the post row (SELECT ... FOR UPDATE) inside a transaction,
* refuses any transition not listed in ALLOWED_TRANSITIONS,
* requires an owner for approve/reject (AI approval never counts),
* records an AuditEvent for owner actions.

Database constraints on LinkedInPost back these rules up independently.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import is_owner
from apps.core.models import AuditAction, AuditEvent
from apps.core.security import redact_secrets

from .models import APPROVAL_REQUIRED_STATUSES, LinkedInPost
from .models import PostStatus as S

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    S.DRAFT: frozenset({S.GENERATING, S.PENDING_APPROVAL, S.REJECTED}),
    S.GENERATING: frozenset({S.REVIEWING, S.FAILED, S.NEEDS_MANUAL_REVIEW}),
    S.REVIEWING: frozenset({S.PENDING_APPROVAL, S.GENERATING, S.NEEDS_MANUAL_REVIEW, S.FAILED}),
    S.PENDING_APPROVAL: frozenset({S.APPROVED, S.REJECTED, S.GENERATING}),
    S.NEEDS_MANUAL_REVIEW: frozenset({S.APPROVED, S.REJECTED, S.GENERATING}),
    S.APPROVED: frozenset({S.PUBLISHING, S.PENDING_APPROVAL, S.REJECTED}),
    # From publishing, an ambiguous outcome goes to needs_manual_review, never back to retry.
    S.PUBLISHING: frozenset({S.PUBLISHED, S.FAILED, S.NEEDS_MANUAL_REVIEW}),
    S.FAILED: frozenset({S.APPROVED, S.GENERATING, S.REJECTED}),
    S.REJECTED: frozenset({S.DRAFT, S.GENERATING}),
    S.PUBLISHED: frozenset(),
}

# Transitions that only the owner may perform.
OWNER_ONLY_TARGETS = frozenset({S.APPROVED, S.REJECTED})


class InvalidTransition(Exception):
    pass


class OwnerRequired(InvalidTransition):
    pass


def can_transition(from_status: str, to_status: str) -> bool:
    return to_status in ALLOWED_TRANSITIONS.get(from_status, frozenset())


def transition_post(
    post: LinkedInPost | int,
    to_status: str,
    *,
    actor: Any = None,
    reason: str = "",
    linkedin_post_urn: str | None = None,
) -> LinkedInPost:
    """Move a post to ``to_status`` safely. Returns the updated, locked-and-saved post."""
    post_id = post.pk if isinstance(post, LinkedInPost) else post
    if to_status not in S.values:
        raise InvalidTransition(f"Unknown status {to_status!r}.")

    with transaction.atomic():
        locked = LinkedInPost.objects.select_for_update().get(pk=post_id)
        from_status = locked.status

        if not can_transition(from_status, to_status):
            raise InvalidTransition(f"Cannot move post from {from_status} to {to_status}.")
        if to_status in OWNER_ONLY_TARGETS and (
            not is_owner(actor) or locked.owner_id != getattr(actor, "pk", None)
        ):
            raise OwnerRequired(f"Only this post's owner can move it to {to_status}.")

        now = timezone.now()
        locked.status = to_status

        if to_status == S.APPROVED:
            locked.approved_by = actor
            locked.approved_at = now
            locked.rejected_at = None
            locked.rejection_reason = ""
        elif to_status not in APPROVAL_REQUIRED_STATUSES:
            # Approval is consumed: leaving the approved path requires a fresh approval.
            locked.approved_by = None
            locked.approved_at = None

        if to_status == S.REJECTED:
            locked.rejected_at = now
            locked.rejection_reason = reason.strip()
        elif to_status == S.PUBLISHED:
            if not linkedin_post_urn:
                raise InvalidTransition("A LinkedIn post URN is required to mark published.")
            locked.linkedin_post_urn = linkedin_post_urn
            locked.published_at = now
            locked.last_error = ""
        elif to_status in (S.FAILED, S.NEEDS_MANUAL_REVIEW) and reason:
            locked.last_error = redact_secrets(reason)

        locked.save()

        if actor is not None and is_owner(actor):
            owner_actions: dict[str, AuditAction] = {
                S.APPROVED: AuditAction.APPROVE,
                S.REJECTED: AuditAction.REJECT,
                S.GENERATING: AuditAction.REGENERATE,
            }
            AuditEvent.objects.create(
                actor=actor,
                action=owner_actions.get(to_status, AuditAction.STATUS_CHANGE),
                post=locked,
                from_status=from_status,
                to_status=to_status,
                metadata={"reason": redact_secrets(reason)} if reason else {},
            )

    return locked
