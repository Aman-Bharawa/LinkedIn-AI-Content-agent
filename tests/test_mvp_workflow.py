import json
from unittest import mock

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.ai.services import DraftGenerationError, GeneratedDraft, generate_linkedin_draft
from apps.posts.models import LinkedInPost, PostStatus, PostVersion, VersionKind


def generated_draft() -> GeneratedDraft:
    return GeneratedDraft(
        topic="Ship smaller releases",
        pillar_slug="startup-product-development",
        hook="Smaller releases make learning cheaper.",
        content="A small release gives a team feedback before assumptions become expensive.",
        hashtags=["startups", "product"],
        model="test-model",
    )


@pytest.mark.django_db
def test_owner_keyword_creates_manual_review_post_without_live_api(client, owner):
    client.force_login(owner)
    with mock.patch(
        "apps.posts.views.generate_linkedin_draft", return_value=generated_draft()
    ) as call:
        response = client.post(reverse("posts:generate"), {"keyword": "small releases"})

    post = LinkedInPost.objects.get()
    assert response.status_code == 302
    assert response.url == reverse("posts:detail", args=[post.pk])
    assert call.call_args.args == ("small releases",)
    assert post.status == PostStatus.NEEDS_MANUAL_REVIEW
    assert post.approved_by is None
    assert post.origin == LinkedInPost.Origin.KEYWORD
    assert post.source_keyword == "small releases"
    assert post.topic == "Ship smaller releases"
    assert post.pillar.slug == "startup-product-development"
    assert post.original_ai_content == generated_draft().content
    assert post.generation_attempts == 1
    version = PostVersion.objects.get(post=post)
    assert version.kind == VersionKind.AI_GENERATED
    assert version.content == post.original_ai_content


@pytest.mark.django_db
def test_owner_can_edit_then_approve_post_with_original_preserved(client, owner, post):
    client.force_login(owner)
    post.original_ai_content = post.content
    post.save()
    PostVersion.objects.create(
        post=post,
        kind=VersionKind.AI_GENERATED,
        topic=post.topic,
        hook=post.hook,
        content=post.content,
        hashtags=post.hashtags,
        ai_model="test-model",
        prompt_version="test-v1",
    )

    response = client.post(
        reverse("posts:edit", args=[post.pk]),
        {
            "topic": "A better topic",
            "hook": "A clearer hook.",
            "content": "Owner-edited body.",
            "hashtags": "#buildinpublic, #product",
        },
    )
    assert response.status_code == 302
    post.refresh_from_db()
    assert post.is_manually_edited
    assert post.content == "Owner-edited body."
    assert post.original_ai_content == "Cut scope until it hurts, then cut once more."
    assert list(post.versions.values_list("kind", flat=True)) == [
        VersionKind.AI_GENERATED,
        VersionKind.OWNER_EDIT,
    ]

    response = client.post(reverse("posts:approve", args=[post.pk]))
    assert response.status_code == 302
    post.refresh_from_db()
    assert post.status == PostStatus.APPROVED
    assert post.approved_by == owner


@pytest.mark.django_db
def test_reject_removes_post_from_queue_but_keeps_history(client, owner, post):
    client.force_login(owner)
    response = client.post(reverse("posts:reject", args=[post.pk]), {"reason": "Needs a source."})

    assert response.status_code == 302
    post.refresh_from_db()
    assert post.status == PostStatus.REJECTED
    assert post.rejection_reason == "Needs a source."
    assert LinkedInPost.objects.filter(pk=post.pk).exists()


def test_dashboard_requires_owner(client):
    assert client.get(reverse("core:home")).status_code == 302


@pytest.mark.django_db
def test_non_owner_cannot_generate_or_review(client, staff_user, post):
    client.force_login(staff_user)
    assert client.get(reverse("core:home")).status_code == 403
    assert client.get(reverse("posts:detail", args=[post.pk])).status_code == 403
    assert client.post(reverse("posts:approve", args=[post.pk])).status_code == 403


@pytest.mark.django_db
@override_settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="test-model")
def test_generation_uses_gemini_structured_output_and_configured_pillars():
    payload = {
        "topic": "Ship smaller releases",
        "pillar_slug": "startup-product-development",
        "hook": "Smaller releases make learning cheaper.",
        "post": "A small release gives a team feedback sooner.",
        "hashtags": ["startups", "product"],
    }
    fake_response = mock.Mock(output_text=json.dumps(payload))
    with mock.patch("apps.ai.services.genai.Client") as sdk:
        sdk.return_value.interactions.create.return_value = fake_response
        result = generate_linkedin_draft("small releases")

    assert result.topic == payload["topic"]
    assert result.pillar_slug == payload["pillar_slug"]
    call = sdk.return_value.interactions.create.call_args.kwargs
    assert call["store"] is False
    assert call["response_format"]["mime_type"] == "application/json"
    assert call["response_format"]["schema"]["properties"]["pillar_slug"]["enum"]


@pytest.mark.django_db
@override_settings(GEMINI_API_KEY="", GEMINI_MODEL="")
def test_generation_fails_safely_when_gemini_is_not_configured():
    with pytest.raises(DraftGenerationError, match="GEMINI_API_KEY"):
        generate_linkedin_draft("AI for startups")


@pytest.mark.django_db
def test_provider_error_moves_post_to_failed_not_the_review_queue(client, owner):
    client.force_login(owner)
    with mock.patch(
        "apps.posts.views.generate_linkedin_draft",
        side_effect=DraftGenerationError("Provider request failed safely."),
    ):
        response = client.post(reverse("posts:generate"), {"keyword": "AI for startups"})

    post = LinkedInPost.objects.get()
    assert response.status_code == 302
    assert post.status == PostStatus.FAILED
    assert post.last_error == "Provider request failed safely."
    assert not LinkedInPost.objects.filter(
        status__in=[PostStatus.PENDING_APPROVAL, PostStatus.NEEDS_MANUAL_REVIEW]
    ).exists()
