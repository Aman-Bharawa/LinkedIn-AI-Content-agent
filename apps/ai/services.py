"""Gemini-backed LinkedIn draft generation using verified project knowledge."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from difflib import SequenceMatcher
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from google import genai
from google.genai import errors

from apps.content.knowledge import load_knowledge_context
from apps.content.models import ContentFormat, ContentPillar, ContentTopic, normalize_title
from apps.content.strategy import InvalidContentStrategy, get_pillar_allocations
from apps.posts.models import LinkedInPost, PostStatus, normalize_hashtags

PROMPT_VERSION = "linkedin-post-v1"
TOPIC_PROMPT_VERSION = "linkedin-topic-v1"
REVIEW_PROMPT_VERSION = "linkedin-review-v1"
MAX_TOPIC_ATTEMPTS = 3
RECENT_TOPIC_DAYS = 90


class DraftGenerationError(Exception):
    """Safe, owner-facing generation failure with no provider diagnostics attached."""


@dataclass(frozen=True)
class GeneratedDraft:
    topic: str
    pillar_slug: str
    hook: str
    content: str
    hashtags: list[str]
    model: str
    prompt_version: str = PROMPT_VERSION
    content_topic_id: int | None = None


@dataclass(frozen=True)
class SelectedTopic:
    title: str
    pillar_slug: str
    angle: str
    post_format: str
    topic_id: int
    model: str
    prompt_version: str = TOPIC_PROMPT_VERSION


@dataclass(frozen=True)
class ReviewResult:
    scores: dict[str, int]
    unsupported_claims: list[str]
    feedback: str
    problems: list[str]
    improvement_suggestions: list[str]
    approved_by_ai: bool
    model: str
    prompt_version: str = REVIEW_PROMPT_VERSION


def _post_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "hook": {"type": "string"},
            "post": {"type": "string"},
            "hashtags": {
                "type": "array",
                "maxItems": 5,
                "items": {"type": "string", "maxLength": 100},
            },
        },
        "required": ["hook", "post", "hashtags"],
    }


def _topic_schema(pillar_slugs: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string"},
            "pillar_slug": {"type": "string", "enum": pillar_slugs},
            "angle": {"type": "string"},
            "format": {"type": "string", "enum": list(ContentFormat.values)},
        },
        "required": ["title", "pillar_slug", "angle", "format"],
    }


def _review_schema() -> dict[str, Any]:
    scores = (
        "authenticity_score",
        "usefulness_score",
        "originality_score",
        "founder_voice_score",
        "clarity_score",
        "hook_quality_score",
        "overall_score",
        "ai_like_score",
        "promotional_score",
        "repetition_score",
    )
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            **{name: {"type": "integer", "minimum": 0, "maximum": 10} for name in scores},
            "unsupported_claims": {"type": "array", "items": {"type": "string"}},
            "feedback": {"type": "string"},
            "problems": {"type": "array", "items": {"type": "string"}},
            "improvement_suggestions": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            *scores,
            "unsupported_claims",
            "feedback",
            "problems",
            "improvement_suggestions",
        ],
    }


def _recent_topic_titles() -> list[str]:
    cutoff = timezone.now() - timedelta(days=RECENT_TOPIC_DAYS)
    stored = list(
        ContentTopic.objects.filter(is_active=True)
        .order_by("-last_used_at", "-created_at")
        .values_list("title", flat=True)[:100]
    )
    posts = list(
        LinkedInPost.objects.filter(created_at__gte=cutoff)
        .exclude(topic="")
        .order_by("-created_at")
        .values_list("topic", flat=True)[:100]
    )
    return list(dict.fromkeys([*stored, *posts]))


def _is_duplicate_topic(title: str, recent_titles: list[str]) -> bool:
    normalized = normalize_title(title)
    if not normalized:
        return True
    candidate_words = set(normalized.split())
    for existing in recent_titles:
        prior = normalize_title(existing)
        if normalized == prior:
            return True
        prior_words = set(prior.split())
        if candidate_words and prior_words:
            overlap = len(candidate_words & prior_words) / len(candidate_words | prior_words)
            if overlap >= 0.8 or SequenceMatcher(None, normalized, prior).ratio() >= 0.88:
                return True
    return False


def select_topic(keyword: str = "") -> SelectedTopic:
    """Select and persist one nonduplicate topic using the configured strategy and knowledge."""
    api_key = settings.GEMINI_API_KEY
    model = settings.GEMINI_MODEL
    if not api_key or not model:
        raise DraftGenerationError("AI generation is not configured. Set GEMINI_API_KEY in .env.")
    try:
        allocations = get_pillar_allocations()
    except InvalidContentStrategy as exc:
        raise DraftGenerationError(
            "The content strategy needs correction before generating."
        ) from exc
    pillar_slugs = list(allocations)
    if not pillar_slugs:
        raise DraftGenerationError("No active content pillars are configured.")
    pillar_names = dict(
        ContentPillar.objects.filter(slug__in=pillar_slugs).values_list("slug", "name")
    )
    recent_titles = _recent_topic_titles()
    recent_json = json.dumps(recent_titles[-50:], ensure_ascii=False)
    strategy_json = json.dumps(
        [
            {"slug": slug, "name": pillar_names[slug], "target_share": str(allocations[slug])}
            for slug in pillar_slugs
        ]
    )
    client = genai.Client(
        api_key=api_key, http_options={"timeout": settings.GEMINI_TIMEOUT_SECONDS * 1000}
    )
    system_prompt = """Choose one useful LinkedIn topic. Follow the supplied verified knowledge
and writing rules. Never invent personal stories, customers, metrics, results, or company facts.
Do not choose a topic that duplicates the recent list. Prefer the owner's keyword when supplied.
Choose one configured pillar and one allowed post format. Return only the requested JSON."""
    prompt = f"""Owner keyword (optional): {keyword.strip() or "(none)"}

Configured pillars and target shares:
{strategy_json}

Allowed formats: {json.dumps(list(ContentFormat.values))}
Recently used topics to avoid: {recent_json}

Verified knowledge and writing rules:
{_knowledge_prompt()}

Return a concise topic title, pillar slug, a specific angle, and format."""
    for _attempt in range(MAX_TOPIC_ATTEMPTS):
        try:
            response = client.interactions.create(
                model=model,
                input=f"{system_prompt}\n\n{prompt}",
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": _topic_schema(pillar_slugs),
                },
                store=False,
            )
        except errors.APIError as exc:
            raise DraftGenerationError(
                "The AI provider could not select a topic. Check the API configuration and retry."
            ) from exc
        try:
            data = json.loads(response.output_text)
            title = str(data["title"]).strip()
            pillar_slug = str(data["pillar_slug"]).strip()
            angle = str(data["angle"]).strip()
            post_format = str(data["format"]).strip()
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise DraftGenerationError(
                "The AI topic response was incomplete. Please retry."
            ) from exc
        if not title or len(title) > 255 or not angle:
            raise DraftGenerationError("The AI topic response was incomplete. Please retry.")
        if pillar_slug not in pillar_slugs or post_format not in ContentFormat.values:
            raise DraftGenerationError("The AI selected an unavailable pillar or post format.")
        if _is_duplicate_topic(title, recent_titles):
            recent_titles.append(title)
            prompt = (
                f"{prompt}\n\nThat selection was too similar to a prior topic. "
                "Choose a clearly different topic."
            )
            continue
        try:
            with transaction.atomic():
                topic = ContentTopic.objects.create(
                    title=title,
                    angle=angle,
                    pillar_id=ContentPillar.objects.only("pk").get(slug=pillar_slug).pk,
                    post_format=post_format,
                )
        except IntegrityError:
            recent_titles.append(title)
            continue
        return SelectedTopic(
            title=topic.title,
            pillar_slug=pillar_slug,
            angle=angle,
            post_format=post_format,
            topic_id=topic.pk,
            model=model,
        )
    raise DraftGenerationError(
        "The AI could not find a sufficiently distinct topic after three attempts. "
        "Please try a different keyword."
    )


def suggest_topic_idea(*, exclude_topic: str = "") -> str:
    """Ask Gemini for a fresh, nonduplicate topic suggestion without saving it."""
    api_key = settings.GEMINI_API_KEY
    model = settings.GEMINI_MODEL
    if not api_key or not model:
        raise DraftGenerationError("AI generation is not configured. Set GEMINI_API_KEY in .env.")
    try:
        allocations = get_pillar_allocations()
    except InvalidContentStrategy as exc:
        raise DraftGenerationError(
            "The content strategy needs correction before generating."
        ) from exc
    pillar_slugs = list(allocations)
    if not pillar_slugs:
        raise DraftGenerationError("No active content pillars are configured.")
    pillar_names = dict(
        ContentPillar.objects.filter(slug__in=pillar_slugs).values_list("slug", "name")
    )
    recent_titles = _recent_topic_titles()
    if exclude_topic.strip():
        recent_titles.append(exclude_topic.strip()[:255])
    strategy_json = json.dumps(
        [
            {"slug": slug, "name": pillar_names[slug], "target_share": str(allocations[slug])}
            for slug in pillar_slugs
        ]
    )
    client = genai.Client(
        api_key=api_key, http_options={"timeout": settings.GEMINI_TIMEOUT_SECONDS * 1000}
    )
    prompt = f"""Suggest one fresh, specific LinkedIn post topic for the account owner.
Choose a configured content pillar, follow the verified knowledge and writing rules, and avoid
topics that duplicate the recent list. Return a concise title that can be used as a topic prompt.
Do not invent personal stories, customers, metrics, results, or company facts.

Configured pillars and target shares:
{strategy_json}

Allowed formats: {json.dumps(list(ContentFormat.values))}
Recently used topics to avoid: {json.dumps(recent_titles[-50:], ensure_ascii=False)}

Verified knowledge and writing rules:
{_knowledge_prompt()}
"""
    for _attempt in range(MAX_TOPIC_ATTEMPTS):
        try:
            response = client.interactions.create(
                model=model,
                input=prompt,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": _topic_schema(pillar_slugs),
                },
                store=False,
            )
        except errors.APIError as exc:
            raise DraftGenerationError(
                "Gemini could not suggest a topic right now. Please try again shortly."
            ) from exc
        try:
            data = json.loads(response.output_text)
            title = str(data["title"]).strip()
            pillar_slug = str(data["pillar_slug"]).strip()
            angle = str(data["angle"]).strip()
            post_format = str(data["format"]).strip()
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise DraftGenerationError(
                "Gemini returned an incomplete topic suggestion. Please try again."
            ) from exc
        if (
            not title
            or len(title) > 255
            or not angle
            or pillar_slug not in pillar_slugs
            or post_format not in ContentFormat.values
        ):
            raise DraftGenerationError(
                "Gemini returned an incomplete topic suggestion. Please try again."
            )
        if not _is_duplicate_topic(title, recent_titles):
            return title
        recent_titles.append(title)
        prompt += "\n\nThat suggestion was too similar to a recent topic. Choose a different one."
    raise DraftGenerationError(
        "Gemini could not find a sufficiently different topic. Please try again."
    )


def _knowledge_prompt() -> str:
    context = load_knowledge_context()
    sections = [
        "Verified founder facts (the only personal facts you may state):\n"
        + ("\n".join(f"- {fact}" for fact in context.founder_facts) or "(none supplied)"),
        "Verified company facts (the only company facts you may state):\n"
        + ("\n".join(f"- {fact}" for fact in context.company_facts) or "(none supplied)"),
        "Approved subject areas:\n"
        + ("\n".join(f"- {area}" for area in context.subject_areas) or "(none configured)"),
        "Owner writing rules:\n" + (context.writing_rules or "(none configured)"),
    ]
    examples = "\n\n".join(
        f"Approved authentic writing sample: {sample.title}\n{sample.content}"
        for sample in context.writing_examples
    )
    sections.append(
        "Authentic voice examples (imitate tone only; do not copy):\n"
        + (examples or "(none supplied)")
    )
    return "\n\n".join(sections)


def _recent_post_context() -> str:
    posts = (
        LinkedInPost.objects.exclude(status=PostStatus.FAILED)
        .exclude(content="")
        .order_by("-created_at")
        .values_list("topic", "content")[:5]
    )
    return json.dumps(
        [{"topic": topic, "content": content[:1200]} for topic, content in posts],
        ensure_ascii=False,
    )


def review_linkedin_post(
    *, topic: str, pillar: str, hook: str, content: str, hashtags: list[str]
) -> ReviewResult:
    """Score draft quality and risk; unsupported claims always block the AI pass result."""
    api_key = settings.GEMINI_API_KEY
    model = settings.GEMINI_MODEL
    if not api_key or not model:
        raise DraftGenerationError("AI generation is not configured. Set GEMINI_API_KEY in .env.")
    system_prompt = """You are a strict LinkedIn editorial reviewer. Assess only the supplied draft.
Quality scores (authenticity, usefulness, originality, founder voice, clarity, hook quality,
overall) are higher-is-better from 0 to 10. Risk scores (AI-like writing, promotional tone,
repetition) are higher-is-worse from 0 to 10. Mark claims unsupported if the supplied verified
knowledge does not support them. Do not infer or invent facts. Give concise actionable feedback.
Return only the requested JSON fields; do not make the final approval decision."""
    prompt = f"""Review this draft.
Topic: {topic}
Pillar: {pillar}
Hook: {hook}
Body: {content}
Hashtags: {json.dumps(hashtags)}

Verified knowledge and writing rules:
{_knowledge_prompt()}

Recent posts for repetition comparison:
{_recent_post_context()}

Score all criteria and return any unsupported factual claims verbatim or by clear summary."""
    try:
        client = genai.Client(
            api_key=api_key, http_options={"timeout": settings.GEMINI_TIMEOUT_SECONDS * 1000}
        )
        response = client.interactions.create(
            model=model,
            input=f"{system_prompt}\n\n{prompt}",
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": _review_schema(),
            },
            store=False,
        )
    except errors.APIError as exc:
        raise DraftGenerationError(
            "The AI provider could not review this draft. Check the API configuration and retry."
        ) from exc
    try:
        data = json.loads(response.output_text)
        score_fields = (
            "authenticity_score",
            "usefulness_score",
            "originality_score",
            "founder_voice_score",
            "clarity_score",
            "hook_quality_score",
            "overall_score",
            "ai_like_score",
            "promotional_score",
            "repetition_score",
        )
        scores: dict[str, int] = {}
        for field in score_fields:
            score = data[field]
            if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 10:
                raise ValueError(f"Invalid {field}")
            scores[field] = score
        unsupported_claims = data["unsupported_claims"]
        problems = data["problems"]
        suggestions = data["improvement_suggestions"]
        feedback = data["feedback"]
        if not all(
            isinstance(items, list) and all(isinstance(item, str) for item in items)
            for items in (unsupported_claims, problems, suggestions)
        ):
            raise ValueError("Review lists must contain strings")
        if not isinstance(feedback, str):
            raise ValueError("Feedback must be text")
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise DraftGenerationError("The AI review response was incomplete. Please retry.") from exc

    approved = (
        not unsupported_claims
        and all(
            scores[field] >= settings.AI_REVIEW_QUALITY_MIN
            for field in (
                "authenticity_score",
                "usefulness_score",
                "originality_score",
                "founder_voice_score",
                "clarity_score",
                "hook_quality_score",
                "overall_score",
            )
        )
        and scores["ai_like_score"] <= settings.AI_REVIEW_AI_LIKE_MAX
        and scores["promotional_score"] <= settings.AI_REVIEW_PROMOTIONAL_MAX
        and scores["repetition_score"] <= settings.AI_REVIEW_REPETITION_MAX
    )
    return ReviewResult(
        scores=scores,
        unsupported_claims=unsupported_claims,
        feedback=feedback,
        problems=problems,
        improvement_suggestions=suggestions,
        approved_by_ai=approved,
        model=model,
    )


def generate_linkedin_post(
    selected_topic: SelectedTopic, *, review_guidance: str = ""
) -> GeneratedDraft:
    """Generate post copy for a saved topic; the model cannot replace its topic or pillar."""
    api_key = settings.GEMINI_API_KEY
    model = settings.GEMINI_MODEL
    if not api_key or not model:
        raise DraftGenerationError("AI generation is not configured. Set GEMINI_API_KEY in .env.")
    topic = ContentTopic.objects.select_related("pillar").filter(pk=selected_topic.topic_id).first()
    if topic is None or topic.title != selected_topic.title:
        raise DraftGenerationError(
            "The selected topic is no longer available. Please select again."
        )
    system_prompt = """You write practical, conversational LinkedIn drafts for a single owner.
Follow the writing rules and factual boundaries supplied below. Do not invent biography,
personal experiences, clients, metrics, revenue, results, or conversations. Do not claim an
unsupported fact. Make the post useful without requiring a call to action. Write short
paragraphs and return only the requested structured fields. Keep the topic and pillar exactly as
provided; never substitute a different topic. The human owner will review every draft.

Project knowledge and writing guidance:
""" + _knowledge_prompt()

    user_prompt = f"""Write one LinkedIn post using this already-selected topic.
Topic: {selected_topic.title}
Pillar: {topic.pillar.name} ({topic.pillar.slug})
Angle: {selected_topic.angle}
Post structure: {selected_topic.post_format}

Recent posts for repetition avoidance (do not copy them):
{_recent_post_context()}

Return a one-sentence hook, body that does not repeat the hook, and up to 5 relevant hashtags
without # characters. Do not repeat the topic as a headline. Do not include unsupported claims;
if the angle would require one, frame it as a general lesson or principle instead."""
    if review_guidance.strip():
        user_prompt += (
            f"\n\nAddress these reviewer notes in this regeneration:\n{review_guidance.strip()}"
        )

    try:
        client = genai.Client(
            api_key=api_key, http_options={"timeout": settings.GEMINI_TIMEOUT_SECONDS * 1000}
        )
        response = client.interactions.create(
            model=model,
            input=f"{system_prompt}\n\n{user_prompt}",
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": _post_schema(),
            },
            store=False,
        )
    except errors.APIError as exc:
        raise DraftGenerationError(
            "The AI provider could not generate a draft. Check the API configuration and retry."
        ) from exc

    try:
        data = json.loads(response.output_text)
        hook = str(data["hook"]).strip()
        content = str(data["post"]).strip()
        hashtags = normalize_hashtags(data["hashtags"])
    except (AttributeError, KeyError, TypeError, ValueError, ValidationError) as exc:
        raise DraftGenerationError(
            "The AI response was incomplete. Please try generating again."
        ) from exc

    if not hook or not content:
        raise DraftGenerationError("The AI response was incomplete. Please try generating again.")
    if len(hashtags) > 5:
        hashtags = hashtags[:5]

    return GeneratedDraft(
        topic=selected_topic.title,
        pillar_slug=selected_topic.pillar_slug,
        hook=hook,
        content=content,
        hashtags=hashtags,
        model=model,
        content_topic_id=selected_topic.topic_id,
    )


def generate_linkedin_draft(keyword: str) -> GeneratedDraft:
    """Select a topic, generate its draft, and mark the topic as used."""
    selected_topic = select_topic(keyword)
    draft = generate_linkedin_post(selected_topic)
    ContentTopic.objects.get(pk=selected_topic.topic_id).mark_used()
    return draft
