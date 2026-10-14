from __future__ import annotations

import re
import unicodedata
from typing import Any

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


class ContentPillar(TimeStampedModel):
    """A content pillar such as "Founder lessons".

    Milestone 3 adds the distribution groups, target weights and the 13 initial
    pillars. Pillars are deactivated, never deleted, so post history stays intact.
    """

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ContentTopic(TimeStampedModel):
    title = models.CharField(max_length=255)
    normalized_title = models.CharField(max_length=255, unique=True, editable=False)
    angle = models.TextField(blank=True, help_text="Description / angle for the topic.")
    pillar = models.ForeignKey(ContentPillar, on_delete=models.PROTECT, related_name="topics")
    used_count = models.PositiveIntegerField(default=0)
    last_used_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
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
        duplicate = ContentTopic.objects.filter(normalized_title=normalized).exclude(pk=self.pk)
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
            # Provenance is mandatory for anything treated as verified fact.
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
