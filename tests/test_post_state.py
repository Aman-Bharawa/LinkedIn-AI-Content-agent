"""Post status state machine (brief §5, §7.3, §7.4)."""

import pytest

from apps.core.models import AuditEvent
from apps.posts.models import LinkedInPost, PostStatus
from apps.posts.state import (
    ALLOWED_TRANSITIONS,
    InvalidTransition,
    OwnerRequired,
    can_transition,
    transition_post,
)

S = PostStatus


def force_status(post, status, **extra):
    LinkedInPost.objects.filter(pk=post.pk).update(status=status, **extra)
    post.refresh_from_db()
    return post


def test_every_status_has_a_transition_entry():
    assert set(ALLOWED_TRANSITIONS) == set(S.values)
    for targets in ALLOWED_TRANSITIONS.values():
        assert targets <= set(S.values)


def test_only_approved_posts_can_move_to_publishing():
    sources = {src for src, targets in ALLOWED_TRANSITIONS.items() if S.PUBLISHING in targets}
    assert sources == {S.APPROVED}


def test_published_is_terminal():
    assert ALLOWED_TRANSITIONS[S.PUBLISHED] == frozenset()


def test_rejected_posts_cannot_publish(post, owner):
    transition_post(post, S.REJECTED, actor=owner, reason="Off-brand")
    with pytest.raises(InvalidTransition):
        transition_post(post, S.PUBLISHING)
    assert not can_transition(S.REJECTED, S.PUBLISHED)


@pytest.mark.parametrize("status", [S.DRAFT, S.PENDING_APPROVAL, S.FAILED, S.NEEDS_MANUAL_REVIEW])
def test_unapproved_posts_cannot_publish(post, status):
    force_status(post, status)
    with pytest.raises(InvalidTransition):
        transition_post(post, S.PUBLISHING)


def test_approval_requires_owner(post, staff_user):
    with pytest.raises(OwnerRequired):
        transition_post(post, S.APPROVED)
    with pytest.raises(OwnerRequired):
        transition_post(post, S.APPROVED, actor=staff_user)
    post.refresh_from_db()
    assert post.status == S.PENDING_APPROVAL


def test_owner_approval_records_approver_and_audit(post, owner):
    approved = transition_post(post, S.APPROVED, actor=owner)
    assert approved.status == S.APPROVED
    assert approved.approved_by == owner
    assert approved.approved_at is not None
    assert approved.is_publishable

    event = AuditEvent.objects.get(post=post)
    assert (event.action, event.from_status, event.to_status) == (
        "approve",
        S.PENDING_APPROVAL,
        S.APPROVED,
    )


def test_happy_path_to_published(post, owner):
    transition_post(post, S.APPROVED, actor=owner)
    transition_post(post, S.PUBLISHING)
    with pytest.raises(InvalidTransition):
        transition_post(post, S.PUBLISHED)
    published = transition_post(post, S.PUBLISHED, linkedin_post_urn="urn:li:share:42")
    assert published.status == S.PUBLISHED
    assert published.published_at is not None
    assert published.approved_by == owner


def test_approval_is_consumed_after_failure(post, owner):
    transition_post(post, S.APPROVED, actor=owner)
    transition_post(post, S.PUBLISHING)
    failed = transition_post(post, S.FAILED, reason="HTTP 500 Authorization: Bearer AQXsecret")
    assert failed.approved_by is None
    assert "AQXsecret" not in failed.last_error
    with pytest.raises(InvalidTransition):
        transition_post(post, S.PUBLISHING)
    with pytest.raises(OwnerRequired):
        transition_post(post, S.APPROVED)


def test_ambiguous_publish_goes_to_manual_review_not_retry(post, owner):
    transition_post(post, S.APPROVED, actor=owner)
    transition_post(post, S.PUBLISHING)
    held = transition_post(post, S.NEEDS_MANUAL_REVIEW, reason="Timeout; outcome unknown")
    assert held.status == S.NEEDS_MANUAL_REVIEW
    assert not can_transition(S.NEEDS_MANUAL_REVIEW, S.PUBLISHING)


def test_rejection_records_reason(post, owner):
    rejected = transition_post(post, S.REJECTED, actor=owner, reason="  Too promotional ")
    assert rejected.rejection_reason == "Too promotional"
    assert rejected.rejected_at is not None
    assert AuditEvent.objects.filter(post=post, action="reject").exists()


def test_unknown_target_status_refused(post):
    with pytest.raises(InvalidTransition):
        transition_post(post, "auto_published")


def test_transition_accepts_post_id(post, owner):
    assert transition_post(post.pk, S.APPROVED, actor=owner).status == S.APPROVED
