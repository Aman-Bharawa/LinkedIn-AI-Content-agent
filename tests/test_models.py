"""Schema rules for content and post models (brief §5, Milestone 1 acceptance)."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.content.models import (
    ContentKnowledge,
    ContentTopic,
    WritingExample,
    normalize_title,
)
from apps.posts.models import (
    MAX_GENERATION_ATTEMPTS,
    QUALITY_SCORE_FIELDS,
    RISK_SCORE_FIELDS,
    SCORE_FIELDS,
    ImmutableFieldError,
    LinkedInPost,
    PostPublication,
    PostReview,
    PostStatus,
    PostVersion,
    normalize_hashtags,
)


def violates(callable_):
    """True if the call raises an IntegrityError (isolated in a savepoint)."""
    try:
        with transaction.atomic():
            callable_()
    except IntegrityError:
        return True
    return False


def set_status(post, status, **extra):
    """Bypass the state machine to test the database constraints directly."""
    return LinkedInPost.objects.filter(pk=post.pk).update(status=status, **extra)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("AI in Startups", "ai in startups"),
        ("AI in Startups!", "  AI   in startups  "),
        ("Café culture", "cafe culture"),
    ],
)
def test_normalize_title_equivalence(a, b):
    assert normalize_title(a) == normalize_title(b)


def test_duplicate_normalized_topic_rejected_by_database(pillar):
    ContentTopic.objects.create(title="Hiring your first engineer", pillar=pillar)
    assert violates(
        lambda: ContentTopic.objects.create(title="hiring your FIRST engineer!", pillar=pillar)
    )


def test_duplicate_topic_rejected_by_validation(pillar):
    ContentTopic.objects.create(title="Hiring your first engineer", pillar=pillar)
    with pytest.raises(ValidationError):
        ContentTopic(title="Hiring your first engineer.", pillar=pillar).full_clean()


def test_topic_title_must_have_words(pillar):
    with pytest.raises(ValidationError):
        ContentTopic(title="!!!", pillar=pillar).full_clean()
    assert violates(lambda: ContentTopic.objects.create(title="???", pillar=pillar))


def test_topic_mark_used(pillar):
    topic = ContentTopic.objects.create(title="Remote teams", pillar=pillar)
    topic.mark_used()
    topic.mark_used()
    topic.refresh_from_db()
    assert topic.used_count == 2
    assert topic.last_used_at is not None


def test_pillar_cannot_be_deleted_while_used(post, pillar):
    from django.db.models import ProtectedError

    with pytest.raises(ProtectedError):
        pillar.delete()


def test_only_active_verified_knowledge_is_usable(db):
    usable = ContentKnowledge.objects.create(
        title="Founded company",
        content="...",
        category="founder",
        source="Owner statement 2026-10-14",
        is_verified=True,
    )
    ContentKnowledge.objects.create(title="Unverified", content="...", category="company")
    ContentKnowledge.objects.create(
        title="Retired",
        content="...",
        category="company",
        source="Old deck",
        is_verified=True,
        is_active=False,
    )
    assert list(ContentKnowledge.objects.usable()) == [usable]
    assert usable.verified_at is not None


def test_knowledge_cannot_be_verified_without_source(db):
    with pytest.raises(ValidationError):
        ContentKnowledge(
            title="Claim", content="...", category="company", is_verified=True
        ).full_clean()
    assert violates(
        lambda: ContentKnowledge.objects.create(
            title="Claim", content="...", category="company", is_verified=True
        )
    )


def test_knowledge_category_enforced(db):
    assert violates(
        lambda: ContentKnowledge.objects.create(title="X", content="...", category="rumour")
    )


def test_only_active_owner_approved_examples_are_usable(db):
    good = WritingExample.objects.create(title="Real post", content="...", is_owner_approved=True)
    WritingExample.objects.create(title="AI draft", content="...")
    WritingExample.objects.create(
        title="Old", content="...", is_owner_approved=True, is_active=False
    )
    assert list(WritingExample.objects.usable()) == [good]


def test_all_brief_statuses_exist():
    assert set(PostStatus.values) == {
        "draft",
        "generating",
        "reviewing",
        "pending_approval",
        "rejected",
        "approved",
        "publishing",
        "published",
        "failed",
        "needs_manual_review",
    }


def test_new_post_defaults_to_draft(db):
    post = LinkedInPost.objects.create(topic="x")
    assert post.status == PostStatus.DRAFT
    assert post.generation_attempts == 0


def test_unknown_status_rejected_by_database(post):
    assert violates(lambda: set_status(post, "auto_published"))


@pytest.mark.parametrize("status", ["approved", "publishing", "published"])
def test_approved_states_require_owner_approval_in_database(post, status):
    assert violates(
        lambda: set_status(
            post, status, linkedin_post_urn="urn:li:share:1", published_at=timezone.now()
        )
    )


def test_published_requires_urn(post, owner):
    assert violates(
        lambda: set_status(post, "published", approved_by=owner, approved_at=timezone.now())
    )


def test_pending_approval_requires_content_and_pillar(db):
    empty = LinkedInPost.objects.create(topic="x")
    assert violates(lambda: set_status(empty, "pending_approval"))


def test_generation_attempts_capped(post):
    assert not violates(
        lambda: LinkedInPost.objects.filter(pk=post.pk).update(
            generation_attempts=MAX_GENERATION_ATTEMPTS
        )
    )
    assert violates(
        lambda: LinkedInPost.objects.filter(pk=post.pk).update(
            generation_attempts=MAX_GENERATION_ATTEMPTS + 1
        )
    )


def test_linkedin_post_urn_unique(post, pillar):
    LinkedInPost.objects.filter(pk=post.pk).update(linkedin_post_urn="urn:li:share:1")
    other = LinkedInPost.objects.create(topic="y", pillar=pillar)
    assert violates(
        lambda: LinkedInPost.objects.filter(pk=other.pk).update(linkedin_post_urn="urn:li:share:1")
    )


def test_original_ai_content_is_write_once(post):
    post.original_ai_content = "First draft"
    post.save()
    post.original_ai_content = "Rewritten"
    with pytest.raises(ImmutableFieldError):
        post.save()

    fresh = LinkedInPost.objects.get(pk=post.pk)
    fresh.original_ai_content = "Rewritten"
    with pytest.raises(ImmutableFieldError):
        fresh.save()
    fresh.original_ai_content = "First draft"
    fresh.content = "Owner edit"
    fresh.save()


def test_hashtags_normalized_and_validated(post):
    post.refresh_from_db()
    assert post.hashtags == ["startups", "product"]
    assert normalize_hashtags(["#AI", "ai", " #Startups "]) == ["AI", "Startups"]
    for bad in (["two words"], ["emoji🚀"], "notalist", [f"t{i}" for i in range(11)]):
        with pytest.raises(ValidationError):
            normalize_hashtags(bad)


def test_last_error_is_redacted(post):
    post.last_error = "LinkedIn 401: Authorization: Bearer AQXsecret999"
    post.save()
    post.refresh_from_db()
    assert "AQXsecret999" not in post.last_error


def test_dashboard_indexes_exist(db):
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, "posts_linkedinpost")
    index_names = {name for name, info in constraints.items() if info["index"]}
    assert {
        "post_status_created_idx",
        "post_scheduled_for_idx",
        "post_published_at_idx",
        "post_pillar_created_idx",
        "post_created_idx",
    } <= index_names


def make_review(post, **overrides):
    values = {name: 7 for name in QUALITY_SCORE_FIELDS} | {name: 2 for name in RISK_SCORE_FIELDS}
    values |= {"attempt_number": 1, "reviewer_model": "test-model", "prompt_version": "v1"}
    values |= overrides
    return PostReview.objects.create(post=post, **values)


def test_score_direction_is_explicit_and_complete():
    assert set(QUALITY_SCORE_FIELDS).isdisjoint(RISK_SCORE_FIELDS)
    assert {"ai_like_score", "promotional_score"} <= set(RISK_SCORE_FIELDS)
    assert {"authenticity_score", "overall_score"} <= set(QUALITY_SCORE_FIELDS)
    for name in SCORE_FIELDS:
        help_text = PostReview._meta.get_field(name).help_text
        expected = "WORSE" if name in RISK_SCORE_FIELDS else "better"
        assert expected in help_text, name


@pytest.mark.parametrize("field", ["overall_score", "ai_like_score"])
def test_review_scores_limited_to_0_10(post, field):
    assert violates(lambda: make_review(post, **{field: 11}))


def test_review_attempt_number_limited(post):
    assert violates(lambda: make_review(post, attempt_number=MAX_GENERATION_ATTEMPTS + 1))


def test_unsupported_claims_block_ai_approval(post):
    review = make_review(post, unsupported_claims=["Grew revenue 10x"], approved_by_ai=False)
    assert review.has_unsupported_claims
    assert violates(lambda: make_review(post, unsupported_claims=["x"], approved_by_ai=True))


def test_versions_are_numbered_and_immutable(post, owner):
    original = PostVersion.objects.create(
        post=post, kind="ai_generated", content="AI text", ai_model="m", prompt_version="v1"
    )
    edit = PostVersion.objects.create(
        post=post, kind="owner_edit", content="Owner text", created_by=owner
    )
    assert (original.version_number, edit.version_number) == (1, 2)

    original.content = "tampered"
    with pytest.raises(ImmutableFieldError):
        original.save()
    assert PostVersion.objects.get(pk=original.pk).content == "AI text"


def test_only_one_original_and_one_final_version(post):
    PostVersion.objects.create(post=post, kind="ai_generated", content="a")
    assert violates(lambda: PostVersion.objects.create(post=post, kind="ai_generated", content="b"))
    PostVersion.objects.create(post=post, kind="published_final", content="c")
    assert violates(
        lambda: PostVersion.objects.create(post=post, kind="published_final", content="d")
    )


def test_one_active_publication_attempt_per_post(post):
    PostPublication.objects.create(post=post, status="in_progress")
    for status in ("pending", "in_progress", "unknown"):
        assert violates(lambda s=status: PostPublication.objects.create(post=post, status=s))
    PostPublication.objects.create(post=post, status="failed")


def test_single_success_and_success_requires_urn(post):
    assert violates(lambda: PostPublication.objects.create(post=post, status="succeeded"))
    PostPublication.objects.create(
        post=post,
        status="succeeded",
        linkedin_post_urn="urn:li:share:1",
        completed_at=timezone.now(),
    )
    assert violates(
        lambda: PostPublication.objects.create(
            post=post,
            status="succeeded",
            linkedin_post_urn="urn:li:share:2",
            completed_at=timezone.now(),
        )
    )


def test_publication_idempotency_key_unique_and_error_redacted(post):
    first = PostPublication.objects.create(
        post=post,
        status="failed",
        http_status=401,
        error_message='{"error": "invalid", "access_token": "AQXsecret777"}',
    )
    assert "AQXsecret777" not in PostPublication.objects.get(pk=first.pk).error_message
    assert violates(
        lambda: PostPublication.objects.create(
            post=post, status="failed", idempotency_key=first.idempotency_key
        )
    )
    assert violates(
        lambda: PostPublication.objects.create(post=post, status="failed", http_status=42)
    )
