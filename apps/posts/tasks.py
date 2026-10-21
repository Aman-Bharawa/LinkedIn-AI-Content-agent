from __future__ import annotations

from datetime import timedelta
from typing import Any

from celery import shared_task
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.ai.services import (
    DraftGenerationError,
    SelectedTopic,
    generate_linkedin_draft,
    generate_linkedin_post,
    review_linkedin_post,
    select_topic,
)
from apps.content.models import ContentPillar, ContentTopic
from apps.core.models import WEEKDAY_AUTO_DRAFT_COUNT, ScheduledDraftRun

from .models import (
    MAX_AUTOMATIC_GENERATION_ATTEMPTS,
    MAX_GENERATION_ATTEMPTS,
    LinkedInPost,
    PostOrigin,
    PostPublication,
    PostReview,
    PostStatus,
    PostVersion,
    PublicationStatus,
    VersionKind,
)
from .state import InvalidTransition, transition_post


def _selected_topic(topic_id: int, model: str) -> SelectedTopic:
    row = ContentTopic.objects.select_related("pillar").get(pk=topic_id)
    return SelectedTopic(
        title=row.title,
        pillar_slug=row.pillar.slug,
        angle=row.angle,
        post_format=row.post_format,
        topic_id=row.pk,
        model=model,
    )


@shared_task(bind=True, name="posts.generate_keyword_post", max_retries=0)
def generate_keyword_post(
    self: Any,
    post_id: int,
    actor_id: int,
    selected_topic_id: int | None = None,
    scheduled_run_id: int | None = None,
) -> str:
    """Generate and review a keyword draft in a deduplicated background job."""
    lock_key = f"post-generation:{post_id}"
    task_id = str(getattr(self.request, "id", "generation"))
    if not cache.add(lock_key, task_id, timeout=360):
        return "generation_already_running"
    User = get_user_model()
    actor = User.objects.filter(pk=actor_id, is_active=True, is_superuser=True).first()
    try:
        post = LinkedInPost.objects.get(pk=post_id)
        if post.status != PostStatus.GENERATING:
            return "post_not_waiting_for_generation"
        if actor is None:
            transition_post(post, PostStatus.FAILED, reason="Project owner is unavailable.")
            _finish_scheduled_post(
                scheduled_run_id,
                ScheduledDraftRun.Status.FAILED,
                "No active project owner is configured.",
                post,
            )
            return "owner_unavailable"
        keyword = post.source_keyword
        selected: SelectedTopic | None = None
        guidance = ""
        for attempt in range(1, MAX_AUTOMATIC_GENERATION_ATTEMPTS + 1):
            try:
                if attempt == 1:
                    if selected_topic_id is None:
                        generated = generate_linkedin_draft(keyword)
                        if generated.content_topic_id is None:
                            raise DraftGenerationError("The selected topic was not saved.")
                        selected = _selected_topic(generated.content_topic_id, generated.model)
                    else:
                        selected = _selected_topic(selected_topic_id, post.ai_model)
                        generated = generate_linkedin_post(selected)
                else:
                    if selected is None:
                        raise DraftGenerationError("The selected topic is unavailable.")
                    generated = generate_linkedin_post(selected, review_guidance=guidance)
            except (DraftGenerationError, ContentTopic.DoesNotExist) as exc:
                transition_post(post, PostStatus.FAILED, actor=actor, reason=str(exc))
                _finish_scheduled_post(
                    scheduled_run_id, ScheduledDraftRun.Status.FAILED, str(exc), post
                )
                return "draft_failed"

            with transaction.atomic():
                post = LinkedInPost.objects.select_for_update().get(pk=post.pk)
                if post.status != PostStatus.GENERATING:
                    return "post_state_changed"
                post.topic = generated.topic
                post.pillar = ContentPillar.objects.get(slug=generated.pillar_slug)
                post.content_topic_id = generated.content_topic_id
                post.hook = generated.hook
                post.content = generated.content
                post.hashtags = generated.hashtags
                if attempt == 1:
                    post.original_ai_content = generated.content
                post.ai_model = generated.model
                post.prompt_version = generated.prompt_version
                post.generation_attempts = attempt
                post.save()
                PostVersion.objects.create(
                    post=post,
                    version_number=attempt,
                    kind=VersionKind.AI_GENERATED if attempt == 1 else VersionKind.AI_REGENERATED,
                    topic=post.topic,
                    hook=post.hook,
                    content=post.content,
                    hashtags=post.hashtags,
                    ai_model=post.ai_model,
                    prompt_version=post.prompt_version,
                )
            post = transition_post(post, PostStatus.REVIEWING, actor=actor)
            try:
                review = review_linkedin_post(
                    topic=generated.topic,
                    pillar=generated.pillar_slug,
                    hook=generated.hook,
                    content=generated.content,
                    hashtags=generated.hashtags,
                )
            except DraftGenerationError as exc:
                transition_post(post, PostStatus.FAILED, actor=actor, reason=str(exc))
                _finish_scheduled_post(
                    scheduled_run_id, ScheduledDraftRun.Status.FAILED, str(exc), post
                )
                return "review_failed"

            with transaction.atomic():
                post = LinkedInPost.objects.select_for_update().get(pk=post.pk)
                post.ai_review_score = review.scores["overall_score"]
                post.ai_review_notes = review.feedback
                post.save()
                PostReview.objects.create(
                    post=post,
                    attempt_number=attempt,
                    **review.scores,
                    has_unsupported_claims=bool(review.unsupported_claims),
                    unsupported_claims=review.unsupported_claims,
                    approved_by_ai=review.approved_by_ai,
                    feedback=review.feedback,
                    problems=review.problems,
                    improvement_suggestions=review.improvement_suggestions,
                    reviewer_model=review.model,
                    prompt_version=review.prompt_version,
                )
            if review.approved_by_ai or attempt == MAX_AUTOMATIC_GENERATION_ATTEMPTS:
                post = transition_post(post, PostStatus.NEEDS_MANUAL_REVIEW, actor=actor)
                _finish_scheduled_post(
                    scheduled_run_id, ScheduledDraftRun.Status.SUCCEEDED, "", post
                )
                return "draft_ready_for_owner_review"
            guidance = "\n".join(
                [review.feedback, *review.problems, *review.improvement_suggestions]
            )
            post = transition_post(post, PostStatus.GENERATING, actor=actor)
        return "draft_failed"
    finally:
        if cache.get(lock_key) == task_id:
            cache.delete(lock_key)


@shared_task(bind=True, name="posts.regenerate_saved_topic", max_retries=0)
def regenerate_saved_topic(self: Any, post_id: int, actor_id: int) -> str:
    """Run one owner-requested regeneration against the same stored topic."""
    lock_key = f"post-generation:{post_id}"
    task_id = str(getattr(self.request, "id", "regeneration"))
    if not cache.add(lock_key, task_id, timeout=360):
        return "generation_already_running"
    User = get_user_model()
    actor = User.objects.filter(pk=actor_id, is_active=True, is_superuser=True).first()
    try:
        post = LinkedInPost.objects.select_related("content_topic__pillar").get(pk=post_id)
        if post.status != PostStatus.GENERATING or post.content_topic_id is None:
            return "post_not_waiting_for_regeneration"
        if actor is None:
            transition_post(post, PostStatus.FAILED, reason="Project owner is unavailable.")
            return "owner_unavailable"
        selected = _selected_topic(post.content_topic_id, post.ai_model)
        attempt = post.generation_attempts
        if attempt < 1 or attempt >= MAX_GENERATION_ATTEMPTS:
            transition_post(
                post,
                PostStatus.FAILED,
                actor=actor,
                reason="The regeneration attempt limit was reached.",
            )
            return "attempt_limit_reached"
        try:
            generated = generate_linkedin_post(selected, review_guidance=post.ai_review_notes)
        except DraftGenerationError as exc:
            transition_post(post, PostStatus.FAILED, actor=actor, reason=str(exc))
            return "draft_failed"
        attempt += 1
        with transaction.atomic():
            post = LinkedInPost.objects.select_for_update().get(pk=post.pk)
            if post.status != PostStatus.GENERATING:
                return "post_state_changed"
            post.topic = generated.topic
            post.pillar = ContentPillar.objects.get(slug=generated.pillar_slug)
            post.content_topic_id = generated.content_topic_id
            post.hook = generated.hook
            post.content = generated.content
            post.hashtags = generated.hashtags
            post.ai_model = generated.model
            post.prompt_version = generated.prompt_version
            post.generation_attempts = attempt
            post.save()
            PostVersion.objects.create(
                post=post,
                version_number=attempt,
                kind=VersionKind.AI_REGENERATED,
                topic=post.topic,
                hook=post.hook,
                content=post.content,
                hashtags=post.hashtags,
                ai_model=post.ai_model,
                prompt_version=post.prompt_version,
            )
        post = transition_post(post, PostStatus.REVIEWING, actor=actor)
        try:
            review = review_linkedin_post(
                topic=generated.topic,
                pillar=generated.pillar_slug,
                hook=generated.hook,
                content=generated.content,
                hashtags=generated.hashtags,
            )
        except DraftGenerationError as exc:
            transition_post(post, PostStatus.FAILED, actor=actor, reason=str(exc))
            return "review_failed"
        with transaction.atomic():
            post = LinkedInPost.objects.select_for_update().get(pk=post.pk)
            post.ai_review_score = review.scores["overall_score"]
            post.ai_review_notes = review.feedback
            post.save()
            PostReview.objects.create(
                post=post,
                attempt_number=attempt,
                **review.scores,
                has_unsupported_claims=bool(review.unsupported_claims),
                unsupported_claims=review.unsupported_claims,
                approved_by_ai=review.approved_by_ai,
                feedback=review.feedback,
                problems=review.problems,
                improvement_suggestions=review.improvement_suggestions,
                reviewer_model=review.model,
                prompt_version=review.prompt_version,
            )
        transition_post(post, PostStatus.NEEDS_MANUAL_REVIEW, actor=actor)
        return "regeneration_ready_for_owner_review"
    finally:
        if cache.get(lock_key) == task_id:
            cache.delete(lock_key)


@shared_task(bind=True, name="posts.create_weekday_auto_draft", max_retries=0)
def create_weekday_auto_draft(self: Any) -> str:
    """Prepare three different topics and queue three owner-review drafts on weekdays."""
    today = timezone.localdate()
    if today.weekday() >= 5:
        return "not_a_weekday"
    User = get_user_model()
    owner = User.objects.filter(is_active=True, is_superuser=True).order_by("pk").first()
    queued = 0
    failed = 0
    already_started = 0

    for run_number in range(1, WEEKDAY_AUTO_DRAFT_COUNT + 1):
        try:
            run, created = ScheduledDraftRun.objects.get_or_create(
                run_date=today, run_number=run_number
            )
        except IntegrityError:
            already_started += 1
            continue
        if not created:
            already_started += 1
            continue
        if owner is None:
            _finish_run(
                run, ScheduledDraftRun.Status.FAILED, "No active project owner is configured."
            )
            failed += 1
            continue

        post: LinkedInPost | None = None
        try:
            selected = select_topic()
            topic = ContentTopic.objects.get(pk=selected.topic_id)
            topic.mark_used()
            post = LinkedInPost.objects.create(
                origin=PostOrigin.AUTO,
                topic=selected.title,
                content_topic=topic,
                pillar=ContentPillar.objects.get(slug=selected.pillar_slug),
                ai_model=selected.model,
                status=PostStatus.DRAFT,
            )
            post = transition_post(post, PostStatus.GENERATING)
            run.post = post
            run.save(update_fields=["post"])
            generate_keyword_post.delay(post.pk, owner.pk, topic.pk, run.pk)
            queued += 1
        except Exception as exc:
            reason = (
                str(exc)
                if isinstance(exc, DraftGenerationError)
                else "The scheduled draft could not be prepared or queued. "
                "Check the worker and AI provider."
            )
            if post and post.status in {PostStatus.GENERATING, PostStatus.REVIEWING}:
                transition_post(post, PostStatus.FAILED, reason=reason)
            _finish_run(run, ScheduledDraftRun.Status.FAILED, reason, post=post)
            failed += 1

    return f"queued_{queued}_failed_{failed}_already_started_{already_started}"


@shared_task(bind=True, name="posts.recover_stale_work", max_retries=0)
def recover_stale_work(self: Any) -> dict[str, int]:
    """Make work left by a dead worker visible and safe for owner recovery."""
    cutoff = timezone.now() - timedelta(minutes=15)
    recovered_generation = 0
    recovered_publication = 0
    recovered_scheduled_queue = LinkedInPost.objects.filter(
        status=PostStatus.APPROVED,
        publication_queued_at__lt=cutoff,
    ).update(
        publication_queued_at=None,
        last_error="The publishing worker did not start; the app will retry this scheduled post.",
        updated_at=timezone.now(),
    )
    for post in LinkedInPost.objects.filter(
        status__in=[PostStatus.GENERATING, PostStatus.REVIEWING], updated_at__lt=cutoff
    ).iterator():
        try:
            transition_post(
                post,
                PostStatus.FAILED,
                reason=(
                    "A background worker stopped before this draft finished. Review and retry it."
                ),
            )
            recovered_generation += 1
        except InvalidTransition:
            # A concurrent worker may have completed between selection and transition.
            continue

    for publication in PostPublication.objects.filter(
        status=PublicationStatus.IN_PROGRESS, started_at__lt=cutoff
    ).select_related("post"):
        try:
            with transaction.atomic():
                locked = PostPublication.objects.select_for_update().get(pk=publication.pk)
                if locked.status != PublicationStatus.IN_PROGRESS:
                    continue
                locked.status = PublicationStatus.UNKNOWN
                locked.error_code = "worker_lost_outcome_unknown"
                locked.error_message = (
                    "The worker stopped during publication. Check LinkedIn before taking action."
                )
                locked.completed_at = timezone.now()
                locked.save()
                transition_post(
                    locked.post,
                    PostStatus.NEEDS_MANUAL_REVIEW,
                    reason=locked.error_message,
                )
                recovered_publication += 1
        except InvalidTransition:
            continue

    stale_runs = ScheduledDraftRun.objects.filter(
        status=ScheduledDraftRun.Status.STARTED, started_at__lt=cutoff
    )
    recovered_runs = stale_runs.count()
    for run in stale_runs:
        _finish_run(
            run,
            ScheduledDraftRun.Status.FAILED,
            "The background worker stopped before the scheduled draft finished.",
            post=run.post,
        )
    return {
        "generation_jobs_recovered": recovered_generation,
        "publications_recovered": recovered_publication,
        "scheduled_publications_requeued": recovered_scheduled_queue,
        "scheduled_runs_recovered": recovered_runs,
    }


def _finish_run(
    run: ScheduledDraftRun,
    status: str,
    error_message: str,
    *,
    post: LinkedInPost | None = None,
) -> None:
    run.status = status
    run.error_message = error_message
    run.post = post
    run.completed_at = timezone.now()
    run.save(update_fields=["status", "error_message", "post", "completed_at"])


def _finish_scheduled_post(
    run_id: int | None, status: str, error_message: str, post: LinkedInPost
) -> None:
    if run_id is None:
        return
    run = ScheduledDraftRun.objects.filter(pk=run_id).first()
    if run:
        _finish_run(run, status, error_message, post=post)
