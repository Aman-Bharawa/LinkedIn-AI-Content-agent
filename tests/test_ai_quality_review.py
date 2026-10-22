import json
from dataclasses import replace
from unittest import mock

import pytest
from django.test import override_settings
from django.urls import reverse
from google.genai import errors

from apps.ai.services import DraftGenerationError, review_linkedin_post
from apps.content.models import ContentTopic
from apps.posts.models import LinkedInPost, PostReview, PostStatus, PostVersion
from tests.test_mvp_workflow import generated_draft, passing_review


def review_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
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
        "unsupported_claims": [],
        "feedback": "Clear and useful.",
        "problems": [],
        "improvement_suggestions": [],
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
@override_settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="test-model")
def test_reviewer_returns_scores_with_configured_directions():
    with mock.patch("apps.ai.services.genai.Client") as sdk:
        sdk.return_value.interactions.create.return_value = mock.Mock(
            output_text=json.dumps(review_payload())
        )

        result = review_linkedin_post(
            topic="Ship smaller releases",
            pillar="startup-product-development",
            hook="Smaller releases make learning cheaper.",
            content="A short body.",
            hashtags=["startup"],
        )

    assert result.approved_by_ai
    assert result.scores["overall_score"] == 8
    assert sdk.return_value.interactions.create.call_args.kwargs["store"] is False


@pytest.mark.django_db
@override_settings(
    GEMINI_API_KEY="test-key",
    GEMINI_MODEL="test-model",
    AI_REVIEW_QUALITY_MIN=7,
    AI_REVIEW_AI_LIKE_MAX=3,
    AI_REVIEW_PROMOTIONAL_MAX=4,
    AI_REVIEW_REPETITION_MAX=4,
)
@pytest.mark.parametrize(
    "changes",
    [
        {"authenticity_score": 6},
        {"ai_like_score": 4},
        {"promotional_score": 5},
        {"repetition_score": 5},
    ],
)
def test_reviewer_applies_quality_minimum_and_risk_maximums(changes):
    with mock.patch("apps.ai.services.genai.Client") as sdk:
        sdk.return_value.interactions.create.return_value = mock.Mock(
            output_text=json.dumps(review_payload(**changes))
        )
        result = review_linkedin_post(
            topic="Scope",
            pillar="founder-lessons",
            hook="A hook.",
            content="The body.",
            hashtags=[],
        )

    assert not result.approved_by_ai


@pytest.mark.django_db
@override_settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="test-model")
def test_unsupported_claims_always_block_ai_pass():
    with mock.patch("apps.ai.services.genai.Client") as sdk:
        sdk.return_value.interactions.create.return_value = mock.Mock(
            output_text=json.dumps(
                review_payload(unsupported_claims=["The team doubled revenue last month."])
            )
        )
        result = review_linkedin_post(
            topic="Growth",
            pillar="founder-lessons",
            hook="A hook.",
            content="The body.",
            hashtags=[],
        )

    assert not result.approved_by_ai
    assert result.unsupported_claims


@pytest.mark.django_db
@override_settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="test-model")
def test_invalid_review_scores_fail_safely():
    with mock.patch("apps.ai.services.genai.Client") as sdk:
        sdk.return_value.interactions.create.return_value = mock.Mock(
            output_text=json.dumps(review_payload(overall_score=11))
        )
        with pytest.raises(DraftGenerationError, match="review response was incomplete"):
            review_linkedin_post(
                topic="Growth",
                pillar="founder-lessons",
                hook="A hook.",
                content="The body.",
                hashtags=[],
            )


@pytest.mark.django_db
@override_settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="test-model")
def test_review_api_error_is_wrapped_in_safe_owner_message():
    with mock.patch("apps.ai.services.genai.Client") as sdk:
        sdk.return_value.interactions.create.side_effect = errors.APIError(
            429, {"error": {"message": "provider internals"}}
        )
        with pytest.raises(DraftGenerationError, match="could not review this draft"):
            review_linkedin_post(
                topic="Growth",
                pillar="founder-lessons",
                hook="A hook.",
                content="The body.",
                hashtags=[],
            )


@pytest.mark.django_db
def test_review_failure_and_low_scores_never_approve_or_publish(client, owner, pillar):
    topic = ContentTopic.objects.create(title="A distinct topic", pillar=pillar)
    failed_scores = {**passing_review().scores, "overall_score": 4}
    failed_review = replace(
        passing_review(), scores=failed_scores, approved_by_ai=False, feedback="Revise the hook."
    )
    client.force_login(owner)
    draft = generated_draft(content_topic_id=topic.pk)
    with (
        mock.patch("apps.posts.tasks.generate_linkedin_draft", return_value=draft),
        mock.patch("apps.posts.tasks.generate_linkedin_post", return_value=draft) as regen,
        mock.patch("apps.posts.tasks.review_linkedin_post", return_value=failed_review),
    ):
        response = client.post(reverse("posts:generate"), {"keyword": "small releases"})

    post = LinkedInPost.objects.get()
    assert response.status_code == 302
    assert post.status == PostStatus.NEEDS_MANUAL_REVIEW
    assert post.generation_attempts == 1
    assert post.reviews.count() == 1
    assert PostReview.objects.filter(post=post, approved_by_ai=True).count() == 0
    assert PostVersion.objects.filter(post=post).count() == 1
    regen.assert_not_called()
    assert not post.is_publishable


@pytest.mark.django_db
def test_provider_review_failure_keeps_generated_draft_but_never_queues_it(client, owner, pillar):
    topic = ContentTopic.objects.create(title="A distinct topic", pillar=pillar)
    client.force_login(owner)
    draft = generated_draft(content_topic_id=topic.pk)
    with (
        mock.patch("apps.posts.tasks.generate_linkedin_draft", return_value=draft),
        mock.patch(
            "apps.posts.tasks.review_linkedin_post",
            side_effect=DraftGenerationError("Safe reviewer failure."),
        ),
    ):
        response = client.post(reverse("posts:generate"), {"keyword": "small releases"})

    post = LinkedInPost.objects.get()
    assert response.status_code == 302
    assert post.status == PostStatus.FAILED
    assert post.content == draft.content
    assert post.reviews.count() == 0
    assert not post.is_publishable
