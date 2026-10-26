from __future__ import annotations

from typing import Any, cast

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import LinkedInAccount
from apps.core.security import SecretValue
from apps.posts.models import (
    ACTIVE_PUBLICATION_STATUSES,
    LinkedInPost,
    PostPublication,
    PostStatus,
    PostVersion,
    PublicationStatus,
    VersionKind,
)
from apps.posts.scheduling import refresh_publication_queue
from apps.posts.state import InvalidTransition, transition_post

from .services import (
    PUBLISH_SCOPES,
    LinkedInAmbiguousError,
    LinkedInServiceError,
    escape_little_text,
    publish_text_post,
)


@shared_task(bind=True, name="linkedin.enqueue_due_publications", max_retries=0)
def enqueue_due_publications(self: Any) -> str:
    """Queue due, owner-approved posts for the existing guarded publisher task."""
    refresh_publication_queue()
    account = LinkedInAccount.objects.filter(is_active=True).first()
    if account is None or account.is_access_token_expired():
        return "linkedin_account_unavailable"
    if not set(PUBLISH_SCOPES).issubset(account.scopes or []):
        return "linkedin_permissions_missing"

    now = timezone.now()
    if PostPublication.objects.filter(status__in=ACTIVE_PUBLICATION_STATUSES).exists():
        return "another_publication_is_active"
    if LinkedInPost.objects.filter(
        status=PostStatus.APPROVED,
        publication_queued_at__isnull=False,
    ).exists():
        return "a_publication_is_already_dispatched"
    due_ids = list(
        LinkedInPost.objects.filter(
            status=PostStatus.APPROVED,
            scheduled_for__lte=now,
            scheduled_for__isnull=False,
            publication_queued_at__isnull=True,
            approved_by__is_active=True,
            approved_by__is_superuser=True,
        )
        .order_by("scheduled_for", "pk")
        .values_list("pk", flat=True)[:1]
    )
    queued = 0
    for post_id in due_ids:
        with transaction.atomic():
            post = LinkedInPost.objects.select_for_update().filter(pk=post_id).first()
            if (
                post is None
                or post.status != PostStatus.APPROVED
                or not post.is_publishable
                or post.scheduled_for is None
                or post.scheduled_for > now
                or post.publication_queued_at is not None
            ):
                continue
            if PostPublication.objects.filter(
                post=post, status__in=ACTIVE_PUBLICATION_STATUSES
            ).exists():
                continue
            post.publication_queued_at = now
            post.save(update_fields=["publication_queued_at", "updated_at"])
            actor_id = post.approved_by_id

        try:
            publish_approved_post.delay(post.pk, actor_id)
        except Exception:
            LinkedInPost.objects.filter(
                pk=post.pk,
                status=PostStatus.APPROVED,
                publication_queued_at=now,
            ).update(
                publication_queued_at=None,
                last_error="The publishing queue was unavailable; the app will retry.",
                updated_at=timezone.now(),
            )
        else:
            queued += 1
    return f"queued_{queued}"


def _release_scheduled_publication(post_id: int, reason: str) -> None:
    LinkedInPost.objects.filter(pk=post_id, status=PostStatus.APPROVED).update(
        publication_queued_at=None,
        last_error=reason,
        updated_at=timezone.now(),
    )


@shared_task(bind=True, name="linkedin.publish_approved_post", max_retries=0)
def publish_approved_post(self: Any, post_id: int, actor_id: int) -> str:
    """Publish one due, owner-approved post; never retry an uncertain result."""
    User = get_user_model()
    actor = User.objects.filter(pk=actor_id, is_active=True, is_superuser=True).first()
    if actor is None:
        _release_scheduled_publication(post_id, "The approving owner account is unavailable.")
        return "owner_unavailable"
    try:
        with transaction.atomic():
            post = LinkedInPost.objects.select_for_update().get(pk=post_id)
            if (
                post.status != PostStatus.APPROVED
                or not post.is_publishable
                or post.approved_by_id != actor.pk
                or post.scheduled_for is None
                or post.scheduled_for > timezone.now()
                or post.publication_queued_at is None
            ):
                return "post_not_due_or_not_queued"
            account = LinkedInAccount.objects.select_for_update().filter(is_active=True).first()
            if account is None:
                _release_scheduled_publication(post.pk, "Connect LinkedIn to publish this post.")
                return "linkedin_not_connected"
            if account.is_access_token_expired():
                _release_scheduled_publication(
                    post.pk, "Reconnect LinkedIn; its access token has expired."
                )
                return "linkedin_reconnect_required"
            if not set(PUBLISH_SCOPES).issubset(account.scopes or []):
                _release_scheduled_publication(
                    post.pk, "Reconnect LinkedIn with permission to publish posts."
                )
                return "linkedin_permissions_missing"
            final_text = "\n\n".join(part for part in (post.hook, post.content) if part)
            if post.hashtags:
                final_text = f"{final_text}\n\n" + " ".join(
                    f"#{tag.lstrip('#')}" for tag in post.hashtags
                )
            final_version = PostVersion.objects.create(
                post=post,
                kind=VersionKind.PUBLISHED_FINAL,
                topic=post.topic,
                hook=post.hook,
                content=post.content,
                hashtags=post.hashtags,
                created_by=actor,
            )
            publication = PostPublication.objects.create(
                post=post,
                version=final_version,
                status=PublicationStatus.IN_PROGRESS,
                started_at=timezone.now(),
                linkedin_api_version=settings.LINKEDIN_API_VERSION,
            )
            post.publication_queued_at = None
            post.last_error = ""
            post.save(update_fields=["publication_queued_at", "last_error", "updated_at"])
            transition_post(post, PostStatus.PUBLISHING, actor=actor)
    except (IntegrityError, InvalidTransition, LinkedInPost.DoesNotExist):
        return "post_changed_or_already_publishing"

    try:
        http_status, urn = publish_text_post(
            access_token=cast(SecretValue, account.access_token).reveal(),
            author_urn=account.member_urn,
            text=escape_little_text(final_text, preserve_hashtag_marker=True),
        )
    except LinkedInAmbiguousError as exc:
        publication.status = PublicationStatus.UNKNOWN
        publication.error_code = "outcome_unknown"
        publication.error_message = str(exc)
        publication.completed_at = timezone.now()
        publication.save()
        transition_post(post, PostStatus.NEEDS_MANUAL_REVIEW, actor=actor, reason=str(exc))
        return "outcome_unknown"
    except LinkedInServiceError as exc:
        publication.status = PublicationStatus.FAILED
        publication.error_code = "linkedin_rejected"
        publication.error_message = str(exc)
        publication.completed_at = timezone.now()
        publication.save()
        transition_post(post, PostStatus.FAILED, actor=actor, reason=str(exc))
        return "publish_failed"

    with transaction.atomic():
        locked_post = LinkedInPost.objects.select_for_update().get(pk=post.pk)
        locked_publication = PostPublication.objects.select_for_update().get(pk=publication.pk)
        locked_publication.status = PublicationStatus.SUCCEEDED
        locked_publication.http_status = http_status
        locked_publication.linkedin_post_urn = urn
        locked_publication.completed_at = timezone.now()
        locked_publication.save()
        transition_post(locked_post, PostStatus.PUBLISHED, actor=actor, linkedin_post_urn=urn)
    return "published"
