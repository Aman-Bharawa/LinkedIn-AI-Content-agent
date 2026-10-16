from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import LinkedInAccount
from apps.content.models import ContentTopic
from apps.core.models import ScheduledDraftRun
from apps.linkedin.services import LinkedInAmbiguousError, OAuthToken
from apps.linkedin.tasks import publish_approved_post
from apps.posts.models import (
    LinkedInPost,
    PostPublication,
    PostStatus,
    PostVersion,
    PublicationStatus,
    VersionKind,
)
from apps.posts.state import transition_post
from apps.posts.tasks import create_weekday_auto_draft, recover_stale_work


@pytest.mark.django_db
def test_oauth_connect_uses_secure_state_and_expected_scopes(client, owner, settings):
    settings.LINKEDIN_CLIENT_ID = "client-example"
    settings.LINKEDIN_REDIRECT_URI = "https://example.test/linkedin/callback/"
    client.force_login(owner)
    response = client.get(reverse("linkedin:connect"))

    assert response.status_code == 302
    assert "scope=openid+profile+w_member_social" in response["Location"]
    assert client.session["linkedin_oauth_state"]


@pytest.mark.django_db
def test_oauth_callback_requires_matching_state(client, owner):
    client.force_login(owner)
    session = client.session
    session["linkedin_oauth_state"] = "expected-state"
    session.save()

    response = client.get(reverse("linkedin:callback"), {"state": "wrong", "code": "code"})

    assert response.status_code == 302
    assert not LinkedInAccount.objects.filter(is_active=True).exists()


@pytest.mark.django_db
def test_oauth_callback_stores_encrypted_account_after_identity_validation(client, owner, settings):
    settings.LINKEDIN_REDIRECT_URI = "https://example.test/linkedin/callback/"
    client.force_login(owner)
    session = client.session
    session["linkedin_oauth_state"] = "expected-state"
    session.save()
    token = OAuthToken(
        access_token="access-value",
        expires_in=3600,
        refresh_token="refresh-value",
        refresh_token_expires_in=86400,
        scopes=("openid", "profile", "w_member_social"),
    )
    with (
        mock.patch("apps.linkedin.views.exchange_code", return_value=token),
        mock.patch(
            "apps.linkedin.views.member_profile",
            return_value=("member-123", "Aman Bharawa"),
        ),
    ):
        response = client.get(
            reverse("linkedin:callback"), {"state": "expected-state", "code": "code"}
        )

    account = LinkedInAccount.objects.get(is_active=True)
    assert response.status_code == 302
    assert account.member_urn == "urn:li:person:member-123"
    assert account.display_name == "Aman Bharawa"
    assert account.access_token.reveal() == "access-value"
    assert account.refresh_token.reveal() == "refresh-value"


@pytest.mark.django_db
def test_publish_task_only_publishes_owner_approved_posts(owner, post, account):
    transition_post(post, PostStatus.APPROVED, actor=owner)
    with mock.patch(
        "apps.linkedin.tasks.publish_text_post", return_value=(201, "urn:li:share:123")
    ) as publish:
        result = publish_approved_post.run(post.pk, owner.pk)

    post.refresh_from_db()
    publication = PostPublication.objects.get(post=post)
    final_version = PostVersion.objects.get(post=post, kind=VersionKind.PUBLISHED_FINAL)
    assert result == "published"
    assert post.status == PostStatus.PUBLISHED
    assert post.linkedin_post_urn == "urn:li:share:123"
    assert publication.status == PublicationStatus.SUCCEEDED
    assert publication.version == final_version
    assert publish.call_args.kwargs["access_token"] == "AQX-test-access-token-value-1234567890"


@pytest.mark.django_db
def test_publish_task_holds_ambiguous_outcome_for_manual_review(owner, post, account):
    transition_post(post, PostStatus.APPROVED, actor=owner)
    with mock.patch(
        "apps.linkedin.tasks.publish_text_post",
        side_effect=LinkedInAmbiguousError("outcome unknown"),
    ):
        result = publish_approved_post.run(post.pk, owner.pk)

    post.refresh_from_db()
    assert result == "outcome_unknown"
    assert post.status == PostStatus.NEEDS_MANUAL_REVIEW
    assert PostPublication.objects.get(post=post).status == PublicationStatus.UNKNOWN


@pytest.mark.django_db
def test_publish_task_refuses_unapproved_posts(owner, post, account):
    with mock.patch("apps.linkedin.tasks.publish_text_post") as publish:
        result = publish_approved_post.run(post.pk, owner.pk)

    assert result == "post_not_approved"
    publish.assert_not_called()


@pytest.mark.django_db
def test_expired_linkedin_account_is_not_used(owner, post, account):
    account.access_token_expires_at = timezone.now() - timedelta(minutes=1)
    account.save(update_fields=["access_token_expires_at"])
    transition_post(post, PostStatus.APPROVED, actor=owner)
    with mock.patch("apps.linkedin.tasks.publish_text_post") as publish:
        result = publish_approved_post.run(post.pk, owner.pk)

    post.refresh_from_db()
    assert result == "linkedin_reconnect_required"
    assert post.status == PostStatus.APPROVED
    publish.assert_not_called()


@pytest.mark.django_db
def test_weekday_scheduler_creates_only_one_auto_draft_per_local_date(owner, pillar, settings):
    from apps.ai.services import ReviewResult

    settings.GEMINI_API_KEY = "test-key"
    draft = SimpleNamespace(
        topic="An auto topic",
        pillar_slug="startup-product-development",
        hook="A small hook.",
        content="A useful body.",
        hashtags=["build"],
        model="test-model",
        prompt_version="test-v1",
        content_topic_id=None,
    )
    review = ReviewResult(
        scores={
            "authenticity_score": 8,
            "usefulness_score": 8,
            "originality_score": 8,
            "founder_voice_score": 8,
            "clarity_score": 8,
            "hook_quality_score": 8,
            "overall_score": 8,
            "ai_like_score": 2,
            "promotional_score": 2,
            "repetition_score": 2,
        },
        unsupported_claims=[],
        feedback="Clear.",
        problems=[],
        improvement_suggestions=[],
        approved_by_ai=True,
        model="test-reviewer",
    )
    topic = ContentTopic.objects.create(title="An auto topic", pillar=pillar)
    draft.content_topic_id = topic.pk
    with (
        mock.patch("apps.posts.tasks.timezone.localdate", return_value=date(2026, 10, 15)),
        mock.patch("apps.posts.tasks.generate_linkedin_draft", return_value=draft),
        mock.patch("apps.posts.tasks.review_linkedin_post", return_value=review),
    ):
        first = create_weekday_auto_draft.run()
        second = create_weekday_auto_draft.run()

    post = LinkedInPost.objects.get(origin="auto")
    assert first == "draft_ready_for_owner_review"
    assert second == "already_succeeded"
    assert post.origin == "auto"
    assert post.status == PostStatus.NEEDS_MANUAL_REVIEW
    assert ScheduledDraftRun.objects.count() == 1
    assert ScheduledDraftRun.objects.get().status == ScheduledDraftRun.Status.SUCCEEDED


@pytest.mark.django_db
def test_recovery_marks_stale_generation_failed(post):
    from apps.posts.models import PostStatus

    post = transition_post(post, PostStatus.GENERATING)
    old = timezone.now() - timedelta(minutes=20)
    LinkedInPost.objects.filter(pk=post.pk).update(updated_at=old)

    result = recover_stale_work.run()

    post.refresh_from_db()
    assert result["generation_jobs_recovered"] == 1
    assert post.status == PostStatus.FAILED
    assert "worker stopped" in post.last_error


@pytest.mark.django_db
def test_recovery_marks_stale_publication_unknown_and_never_retries(owner, post, account):
    from apps.posts.models import PostStatus

    transition_post(post, PostStatus.APPROVED, actor=owner)
    post = transition_post(post, PostStatus.PUBLISHING, actor=owner)
    version = PostVersion.objects.create(
        post=post,
        kind=VersionKind.PUBLISHED_FINAL,
        topic=post.topic,
        hook=post.hook,
        content=post.content,
        hashtags=post.hashtags,
        created_by=owner,
    )
    publication = PostPublication.objects.create(
        post=post,
        version=version,
        status=PublicationStatus.IN_PROGRESS,
        started_at=timezone.now() - timedelta(minutes=20),
    )

    result = recover_stale_work.run()

    post.refresh_from_db()
    publication.refresh_from_db()
    assert result["publications_recovered"] == 1
    assert post.status == PostStatus.NEEDS_MANUAL_REVIEW
    assert publication.status == PublicationStatus.UNKNOWN
