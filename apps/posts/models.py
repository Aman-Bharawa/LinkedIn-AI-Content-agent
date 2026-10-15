from __future__ import annotations

import re
import uuid
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TimeStampedModel
from apps.core.security import redact_secrets

# Brief M6: maximum three generation attempts per post.
MAX_GENERATION_ATTEMPTS = 3

SCORE_MIN = 0
SCORE_MAX = 10

HASHTAG_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,100}$")
MAX_HASHTAGS = 10


class PostStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    GENERATING = "generating", "Generating"
    REVIEWING = "reviewing", "Reviewing"
    PENDING_APPROVAL = "pending_approval", "Pending approval"
    REJECTED = "rejected", "Rejected"
    APPROVED = "approved", "Approved"
    PUBLISHING = "publishing", "Publishing"
    PUBLISHED = "published", "Published"
    FAILED = "failed", "Failed"
    NEEDS_MANUAL_REVIEW = "needs_manual_review", "Needs manual review"


class PostOrigin(models.TextChoices):
    KEYWORD = "keyword", "Keyword"
    AUTO = "auto", "Auto draft"


# Statuses where the post must have real content and a pillar.
CONTENT_REQUIRED_STATUSES = (
    PostStatus.PENDING_APPROVAL,
    PostStatus.APPROVED,
    PostStatus.PUBLISHING,
    PostStatus.PUBLISHED,
)
# Statuses only reachable after an explicit owner approval.
APPROVAL_REQUIRED_STATUSES = (
    PostStatus.APPROVED,
    PostStatus.PUBLISHING,
    PostStatus.PUBLISHED,
)


def normalize_hashtags(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or not all(isinstance(tag, str) for tag in value):
        raise ValidationError("Hashtags must be a list of strings.")
    tags: list[str] = []
    for raw in value:
        tag = raw.strip().lstrip("#")
        if not HASHTAG_PATTERN.match(tag):
            raise ValidationError(
                f"Invalid hashtag {raw!r}: use letters, numbers and underscores only."
            )
        if tag.casefold() not in {existing.casefold() for existing in tags}:
            tags.append(tag)
    if len(tags) > MAX_HASHTAGS:
        raise ValidationError(f"At most {MAX_HASHTAGS} hashtags are allowed.")
    return tags


class ImmutableFieldError(ValidationError):
    pass


class LinkedInPost(TimeStampedModel):
    Status = PostStatus
    Origin = PostOrigin

    topic = models.CharField(max_length=255, blank=True)
    origin = models.CharField(max_length=16, choices=PostOrigin.choices, default=PostOrigin.KEYWORD)
    source_keyword = models.CharField(max_length=255, blank=True)
    content_topic = models.ForeignKey(
        "content.ContentTopic",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="posts",
    )
    pillar = models.ForeignKey(
        "content.ContentPillar",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="posts",
    )
    hook = models.TextField(blank=True)
    content = models.TextField(blank=True)
    hashtags = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=32, choices=PostStatus.choices, default=PostStatus.DRAFT)

    ai_review_score = models.PositiveSmallIntegerField(null=True, blank=True)
    ai_review_notes = models.TextField(blank=True)
    generation_attempts = models.PositiveSmallIntegerField(default=0)

    rejection_reason = models.TextField(blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="approved_posts",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    linkedin_post_urn = models.CharField(max_length=255, null=True, blank=True, unique=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    is_manually_edited = models.BooleanField(default=False)
    original_ai_content = models.TextField(
        blank=True, help_text="First AI-generated body. Write-once; never overwritten."
    )
    ai_model = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=50, blank=True)

    last_error = models.TextField(blank=True, help_text="Safe, owner-facing error message.")

    class Meta:
        verbose_name = "LinkedIn post"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=PostStatus.values),
                name="post_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(generation_attempts__lte=MAX_GENERATION_ATTEMPTS),
                name="post_generation_attempts_max",
            ),
            models.CheckConstraint(
                condition=models.Q(ai_review_score__isnull=True)
                | models.Q(ai_review_score__gte=SCORE_MIN, ai_review_score__lte=SCORE_MAX),
                name="post_ai_review_score_range",
            ),
            models.CheckConstraint(
                condition=~models.Q(status__in=CONTENT_REQUIRED_STATUSES)
                | (models.Q(pillar__isnull=False) & ~models.Q(content="")),
                name="post_content_required_for_review_and_publish",
            ),
            # Database-level guarantee: nothing is approved/publishing/published
            # without a recorded owner approval.
            models.CheckConstraint(
                condition=~models.Q(status__in=APPROVAL_REQUIRED_STATUSES)
                | models.Q(approved_by__isnull=False, approved_at__isnull=False),
                name="post_owner_approval_required",
            ),
            models.CheckConstraint(
                condition=~models.Q(status=PostStatus.PUBLISHED)
                | models.Q(linkedin_post_urn__isnull=False, published_at__isnull=False),
                name="post_published_requires_urn",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="post_status_created_idx"),
            models.Index(fields=["origin", "-created_at"], name="post_origin_created_idx"),
            models.Index(fields=["scheduled_for"], name="post_scheduled_for_idx"),
            models.Index(fields=["published_at"], name="post_published_at_idx"),
            models.Index(fields=["pillar", "-created_at"], name="post_pillar_created_idx"),
            models.Index(fields=["-created_at"], name="post_created_idx"),
        ]

    def __str__(self) -> str:
        return self.topic or f"Post #{self.pk}"

    # Value of original_ai_content as loaded from the database (write-once guard).
    _loaded_original_ai_content: str | None = None

    @classmethod
    def from_db(cls, *args: Any, **kwargs: Any) -> LinkedInPost:
        instance = super().from_db(*args, **kwargs)
        instance._loaded_original_ai_content = instance.__dict__.get("original_ai_content")
        return instance

    def clean(self) -> None:
        try:
            self.hashtags = normalize_hashtags(self.hashtags)
        except ValidationError as exc:
            raise ValidationError({"hashtags": exc.messages}) from exc

    def save(self, *args: Any, **kwargs: Any) -> None:
        loaded = getattr(self, "_loaded_original_ai_content", None)
        if loaded and self.original_ai_content != loaded:
            raise ImmutableFieldError("original_ai_content is write-once and cannot be changed.")
        self.hashtags = normalize_hashtags(self.hashtags)
        self.last_error = redact_secrets(self.last_error)
        super().save(*args, **kwargs)
        self._loaded_original_ai_content = self.original_ai_content

    @property
    def is_publishable(self) -> bool:
        return self.status == PostStatus.APPROVED and self.approved_by_id is not None


# --- Reviews -----------------------------------------------------------------

# Score direction (brief §5): quality scores are higher-is-better,
# risk scores are higher-is-worse. Thresholds are configured in Milestone 6.
QUALITY_SCORE_FIELDS = (
    "authenticity_score",
    "usefulness_score",
    "originality_score",
    "founder_voice_score",
    "clarity_score",
    "hook_quality_score",
    "overall_score",
)
RISK_SCORE_FIELDS = (
    "ai_like_score",
    "promotional_score",
    "repetition_score",
)
SCORE_FIELDS = QUALITY_SCORE_FIELDS + RISK_SCORE_FIELDS


def _score_field(help_text: str) -> models.PositiveSmallIntegerField:
    return models.PositiveSmallIntegerField(help_text=f"{SCORE_MIN}-{SCORE_MAX}. {help_text}")


def _score_constraints() -> list[models.CheckConstraint]:
    return [
        models.CheckConstraint(
            condition=models.Q(**{f"{name}__gte": SCORE_MIN, f"{name}__lte": SCORE_MAX}),
            name=f"review_{name}_range",
        )
        for name in SCORE_FIELDS
    ]


class PostReview(models.Model):
    post = models.ForeignKey(LinkedInPost, on_delete=models.CASCADE, related_name="reviews")
    attempt_number = models.PositiveSmallIntegerField()

    authenticity_score = _score_field("Higher is better.")
    usefulness_score = _score_field("Higher is better.")
    originality_score = _score_field("Higher is better.")
    founder_voice_score = _score_field("Higher is better.")
    clarity_score = _score_field("Higher is better.")
    hook_quality_score = _score_field("Higher is better.")
    overall_score = _score_field("Higher is better.")
    ai_like_score = _score_field("Higher is WORSE (more AI-sounding).")
    promotional_score = _score_field("Higher is WORSE (more promotional).")
    repetition_score = _score_field("Higher is WORSE (more repetitive of recent posts).")

    has_unsupported_claims = models.BooleanField(default=False)
    unsupported_claims = models.JSONField(default=list, blank=True)
    approved_by_ai = models.BooleanField(default=False)
    feedback = models.TextField(blank=True)
    problems = models.JSONField(default=list, blank=True)
    improvement_suggestions = models.JSONField(default=list, blank=True)

    reviewer_model = models.CharField(max_length=100)
    prompt_version = models.CharField(max_length=50)
    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["post", "-reviewed_at"]
        constraints = [
            *_score_constraints(),
            models.CheckConstraint(
                condition=models.Q(
                    attempt_number__gte=1, attempt_number__lte=MAX_GENERATION_ATTEMPTS
                ),
                name="review_attempt_number_range",
            ),
            # An unsupported claim always blocks AI approval.
            models.CheckConstraint(
                condition=~models.Q(approved_by_ai=True, has_unsupported_claims=True),
                name="review_unsupported_claims_block_ai_approval",
            ),
        ]
        indexes = [
            models.Index(fields=["post", "-reviewed_at"], name="review_post_reviewed_idx"),
        ]

    def __str__(self) -> str:
        return f"Review of post #{self.post_id} (attempt {self.attempt_number})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.unsupported_claims:
            self.has_unsupported_claims = True
        super().save(*args, **kwargs)


# --- Versions ----------------------------------------------------------------


class VersionKind(models.TextChoices):
    AI_GENERATED = "ai_generated", "AI generated"
    AI_REGENERATED = "ai_regenerated", "AI regenerated"
    OWNER_EDIT = "owner_edit", "Owner edit"
    PUBLISHED_FINAL = "published_final", "Final content sent for publication"


class PostVersion(models.Model):
    """Immutable snapshot of a post's content. Never updated after creation."""

    Kind = VersionKind

    post = models.ForeignKey(LinkedInPost, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField()
    kind = models.CharField(max_length=32, choices=VersionKind.choices)
    topic = models.CharField(max_length=255, blank=True)
    hook = models.TextField(blank=True)
    content = models.TextField()
    hashtags = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    ai_model = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["post", "version_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["post", "version_number"], name="version_unique_number_per_post"
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=VersionKind.values), name="version_kind_valid"
            ),
            models.UniqueConstraint(
                fields=["post"],
                condition=models.Q(kind=VersionKind.AI_GENERATED),
                name="version_single_original_per_post",
            ),
            models.UniqueConstraint(
                fields=["post"],
                condition=models.Q(kind=VersionKind.PUBLISHED_FINAL),
                name="version_single_final_per_post",
            ),
        ]

    def __str__(self) -> str:
        return f"Post #{self.post_id} v{self.version_number} ({self.kind})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableFieldError("Post versions are immutable.")
        self.hashtags = normalize_hashtags(self.hashtags)
        if self.version_number is None:
            last = (
                PostVersion.objects.filter(post_id=self.post_id)
                .order_by("-version_number")
                .values_list("version_number", flat=True)
                .first()
            )
            self.version_number = (last or 0) + 1
        super().save(*args, **kwargs)


# --- Publications ------------------------------------------------------------


class PublicationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In progress"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    UNKNOWN = "unknown", "Outcome unknown (needs reconciliation)"


# An attempt in any of these states blocks starting another one for the same post.
ACTIVE_PUBLICATION_STATUSES = (
    PublicationStatus.PENDING,
    PublicationStatus.IN_PROGRESS,
    PublicationStatus.UNKNOWN,
)


class PostPublication(TimeStampedModel):
    Status = PublicationStatus

    post = models.ForeignKey(LinkedInPost, on_delete=models.PROTECT, related_name="publications")
    version = models.ForeignKey(
        PostVersion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="publications",
        help_text="Exact content snapshot sent to LinkedIn.",
    )
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(
        max_length=32, choices=PublicationStatus.choices, default=PublicationStatus.PENDING
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True, help_text="Redacted; never contains tokens.")
    linkedin_post_urn = models.CharField(max_length=255, null=True, blank=True, unique=True)
    linkedin_api_version = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=PublicationStatus.values),
                name="publication_status_valid",
            ),
            models.UniqueConstraint(
                fields=["post"],
                condition=models.Q(status__in=ACTIVE_PUBLICATION_STATUSES),
                name="publication_single_active_attempt_per_post",
            ),
            models.UniqueConstraint(
                fields=["post"],
                condition=models.Q(status=PublicationStatus.SUCCEEDED),
                name="publication_single_success_per_post",
            ),
            models.CheckConstraint(
                condition=~models.Q(status=PublicationStatus.SUCCEEDED)
                | models.Q(linkedin_post_urn__isnull=False, completed_at__isnull=False),
                name="publication_success_requires_urn",
            ),
            models.CheckConstraint(
                condition=models.Q(http_status__isnull=True)
                | models.Q(http_status__gte=100, http_status__lte=599),
                name="publication_http_status_range",
            ),
        ]
        indexes = [
            models.Index(fields=["post", "-created_at"], name="publication_post_created_idx"),
            models.Index(fields=["status"], name="publication_status_idx"),
        ]

    def __str__(self) -> str:
        return f"Publication of post #{self.post_id} ({self.status})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.error_message = redact_secrets(self.error_message)
        super().save(*args, **kwargs)
