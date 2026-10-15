import json
from unittest import mock

import pytest
from django.test import override_settings

from apps.ai.services import DraftGenerationError, select_topic
from apps.content.models import ContentTopic


def response(title: str, *, pillar: str = "founder-lessons") -> mock.Mock:
    return mock.Mock(
        output_text=json.dumps(
            {
                "title": title,
                "pillar_slug": pillar,
                "angle": "A practical decision rule for a small team.",
                "format": "framework",
            }
        )
    )


@pytest.mark.django_db
@override_settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="test-model")
def test_select_topic_persists_a_structured_topic_and_uses_configured_pillars():
    with mock.patch("apps.ai.services.genai.Client") as sdk:
        sdk.return_value.interactions.create.return_value = response(
            "A decision rule for early product scope"
        )

        selected = select_topic("product scope")

    topic = ContentTopic.objects.get(pk=selected.topic_id)
    assert selected.title == topic.title
    assert topic.pillar.slug == "founder-lessons"
    assert topic.post_format == "framework"
    call = sdk.return_value.interactions.create.call_args.kwargs
    assert call["store"] is False
    assert call["response_format"]["schema"]["properties"]["pillar_slug"]["enum"]
    assert "product scope" in call["input"]


@pytest.mark.django_db
@override_settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="test-model")
def test_select_topic_retries_exact_and_near_duplicates_then_records_new_topic(pillar):
    ContentTopic.objects.create(title="A practical rule for early product scope", pillar=pillar)
    with mock.patch("apps.ai.services.genai.Client") as sdk:
        sdk.return_value.interactions.create.side_effect = [
            response("A practical rule for early product scope!"),
            response("A practical rule for product scope"),
            response("Why small teams should validate before expanding scope"),
        ]

        selected = select_topic("product scope")

    assert selected.title == "Why small teams should validate before expanding scope"
    assert sdk.return_value.interactions.create.call_count == 3
    assert ContentTopic.objects.count() == 2


@pytest.mark.django_db
@override_settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="test-model")
def test_select_topic_stops_after_three_duplicate_candidates(pillar):
    ContentTopic.objects.create(title="Founders and product scope", pillar=pillar)
    with mock.patch("apps.ai.services.genai.Client") as sdk:
        sdk.return_value.interactions.create.side_effect = [
            response("Founders and product scope!"),
            response("Founders and product scope"),
            response("Product scope and founders"),
        ]

        with pytest.raises(DraftGenerationError, match="three attempts"):
            select_topic("product scope")

    assert sdk.return_value.interactions.create.call_count == 3
    assert ContentTopic.objects.count() == 1


@pytest.mark.django_db
@override_settings(GEMINI_API_KEY="", GEMINI_MODEL="")
def test_select_topic_fails_safely_without_credentials():
    with pytest.raises(DraftGenerationError, match="GEMINI_API_KEY"):
        select_topic("AI")
