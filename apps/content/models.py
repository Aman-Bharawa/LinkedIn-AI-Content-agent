from __future__ import annotations

import re
import unicodedata
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


def normalize_title(title: str) -> str:
    """Canonical form used to detect exact duplicate topics.

    Case-folds, strips accents and punctuation, and collapses whitespace, so
    "AI in Startups!" and "  ai in   startups" are the same topic.
    """
    text = unicodedata.normalize("NFKD", title).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class ContentGroup(TimeStampedModel):
    """A weighted strategy group that contains one or more content pillars."""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    target_share = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text=(
            "Target percentage of posts allocated to this group; active groups must total 100%."
        ),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(target_share__gte=0, target_share__lte=100),
                name="content_group_share_range",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class ContentPillar(TimeStampedModel):
    """A content pillar such as "Founder lessons".

    Pillars are deactivated, never deleted, so post history stays intact.
    """

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    group = models.ForeignKey(ContentGroup, on_delete=models.PROTECT, related_name="pillars")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def target_share(self):
        """Equal share within its group, based on active pillars only."""
        from decimal import Decimal

        if not self.is_active:
            return Decimal("0")
        slugs = list(
            self.group.pillars.filter(is_active=True)
            .order_by("name")
            .values_list("slug", flat=True)
        )
        if not slugs:
            return Decimal("0")
        cents = int(self.group.target_share * 100)
        base, remainder = divmod(cents, len(slugs))
        index = slugs.index(self.slug)
        return Decimal(base + (1 if index < remainder else 0)) / 100


class ContentFormat(models.TextChoices):
    LESSON = "lesson", "Lesson"
    HOW_TO = "how_to", "How to"
    CONTRARIAN = "contrarian", "Contrarian"
    FRAMEWORK = "framework", "Framework"
    MYTH_VS_REALITY = "myth_vs_reality", "Myth vs reality"
    QUESTION = "question", "Question"


class ContentTopic(TimeStampedModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="content_topics",
        help_text="Null means a shared, system-provided topic.",
    )
    title = models.CharField(max_length=255)
    normalized_title = models.CharField(max_length=255, editable=False)
    angle = models.TextField(blank=True, help_text="Description / angle for the topic.")
    post_format = models.CharField(
        max_length=32, choices=ContentFormat.choices, default=ContentFormat.LESSON
    )
    pillar = models.ForeignKey(ContentPillar, on_delete=models.PROTECT, related_name="topics")
    used_count = models.PositiveIntegerField(default=0)
    last_used_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["normalized_title"],
                condition=models.Q(owner__isnull=True),
                name="content_topic_global_title_uniq",
            ),
            models.UniqueConstraint(
                fields=["owner", "normalized_title"],
                condition=models.Q(owner__isnull=False),
                name="content_topic_owner_title_uniq",
            ),
            models.CheckConstraint(
                condition=~models.Q(normalized_title=""),
                name="content_topic_normalized_title_not_empty",
            ),
        ]
        indexes = [
            models.Index(fields=["is_active", "last_used_at"], name="topic_active_last_used_idx"),
            models.Index(fields=["pillar", "is_active"], name="topic_pillar_active_idx"),
        ]

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        normalized = normalize_title(self.title or "")
        if not normalized:
            raise ValidationError({"title": "Title must contain letters or numbers."})
        duplicate = ContentTopic.objects.filter(
            normalized_title=normalized, owner_id=self.owner_id
        ).exclude(pk=self.pk)
        if duplicate.exists():
            raise ValidationError({"title": "A topic with this title already exists."})

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.title = (self.title or "").strip()
        self.normalized_title = normalize_title(self.title)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "title" in update_fields:
            kwargs["update_fields"] = {*update_fields, "normalized_title"}
        super().save(*args, **kwargs)

    def mark_used(self, when: Any = None) -> None:
        """Record that this topic was selected for a post."""
        self.used_count = models.F("used_count") + 1
        self.last_used_at = when or timezone.now()
        self.save(update_fields=["used_count", "last_used_at", "updated_at"])
        self.refresh_from_db(fields=["used_count"])


class KnowledgeQuerySet(models.QuerySet["ContentKnowledge"]):
    def usable(self) -> KnowledgeQuerySet:
        """Only active, verified knowledge may be used as factual support."""
        return self.filter(is_active=True, is_verified=True)


class KnowledgeCategory(models.TextChoices):
    FOUNDER = "founder", "Founder"
    COMPANY = "company", "Company"
    WRITING_STYLE = "writing_style", "Writing style"
    SUBJECT_AREA = "subject_area", "Approved subject area"
    OTHER = "other", "Other"


class ContentKnowledge(TimeStampedModel):
    Category = KnowledgeCategory

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="content_knowledge",
        help_text="Null means shared Markdown knowledge; otherwise this belongs to one user.",
    )
    title = models.CharField(max_length=255)
    content = models.TextField()
    category = models.CharField(max_length=32, choices=KnowledgeCategory.choices)
    source = models.CharField(
        max_length=500,
        blank=True,
        help_text="Where this fact came from (document, owner statement, URL). "
        "Required before it can be verified.",
    )
    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    objects = KnowledgeQuerySet.as_manager()

    class Meta:
        verbose_name = "content knowledge"
        verbose_name_plural = "content knowledge"
        ordering = ["category", "title"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(category__in=KnowledgeCategory.values),
                name="knowledge_category_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(is_verified=False)
                | (~models.Q(source="") & models.Q(verified_at__isnull=False)),
                name="knowledge_verified_requires_source",
            ),
        ]
        indexes = [
            models.Index(
                fields=["category", "is_active", "is_verified"], name="knowledge_usable_idx"
            ),
        ]

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        if self.is_verified and not (self.source or "").strip():
            raise ValidationError({"source": "A source is required before marking verified."})

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.is_verified and self.verified_at is None:
            self.verified_at = timezone.now()
        if not self.is_verified:
            self.verified_at = None
        super().save(*args, **kwargs)


class WritingExampleQuerySet(models.QuerySet["WritingExample"]):
    def usable(self) -> WritingExampleQuerySet:
        """Only active, owner-approved authentic examples may be used as samples."""
        return self.filter(is_active=True, is_owner_approved=True)


class WritingExample(TimeStampedModel):
    title = models.CharField(max_length=255)
    content = models.TextField()
    category = models.CharField(max_length=100, blank=True)
    is_owner_approved = models.BooleanField(
        default=False,
        help_text="Confirmed by the owner as authentic writing. Never set for AI output.",
    )
    is_active = models.BooleanField(default=True)

    objects = WritingExampleQuerySet.as_manager()

    class Meta:
        ordering = ["title"]
        indexes = [
            models.Index(
                fields=["is_active", "is_owner_approved"], name="writing_example_usable_idx"
            ),
        ]

    def __str__(self) -> str:
        return self.title
