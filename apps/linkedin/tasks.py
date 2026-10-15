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
    LinkedInPost,
    PostPublication,
    PostStatus,
    PostVersion,
    PublicationStatus,
    VersionKind,
)
from apps.posts.state import InvalidTransition, transition_post

from .services import (
    PUBLISH_SCOPES,
    LinkedInAmbiguousError,
    LinkedInServiceError,
    publish_text_post,
)


@shared_task(bind=True, name="linkedin.publish_approved_post", max_retries=0)
def publish_approved_post(self: Any, post_id: int, actor_id: int) -> str:
    """Publish one explicitly owner-approved text post; never retry an uncertain result."""
    User = get_user_model()
    actor = User.objects.filter(pk=actor_id, is_active=True, is_superuser=True).first()
    if actor is None:
        return "owner_unavailable"
    try:
        with transaction.atomic():
            post = LinkedInPost.objects.select_for_update().get(pk=post_id)
            if post.status != PostStatus.APPROVED or not post.is_publishable:
                return "post_not_approved"
            account = LinkedInAccount.objects.select_for_update().filter(is_active=True).first()
            if account is None:
                return "linkedin_not_connected"
            if account.is_access_token_expired():
                return "linkedin_reconnect_required"
            if not set(PUBLISH_SCOPES).issubset(account.scopes or []):
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
            transition_post(post, PostStatus.PUBLISHING, actor=actor)
    except (IntegrityError, InvalidTransition, LinkedInPost.DoesNotExist):
        return "post_changed_or_already_publishing"

    try:
        http_status, urn = publish_text_post(
            access_token=cast(SecretValue, account.access_token).reveal(),
            author_urn=account.member_urn,
            text=final_text,
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
