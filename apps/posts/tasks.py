from __future__ import annotations

import random
from contextlib import suppress
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
)
from apps.content.models import ContentPillar, ContentTopic
from apps.core.models import MORNING_DRAFT_COUNT, MorningTopic, ScheduledDraftRun

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
    use_cache_lock: bool = True,
) -> str:
    """Generate and review a keyword draft in a deduplicated background job."""
    lock_key = f"post-generation:{post_id}"
    task_id = str(getattr(self.request, "id", "generation"))
    if use_cache_lock and not cache.add(lock_key, task_id, timeout=360):
        return "generation_already_running"
    User = get_user_model()
    actor = User.objects.filter(pk=actor_id, is_active=True).first()
    try:
        post = LinkedInPost.objects.get(pk=post_id)
        if post.status != PostStatus.GENERATING or post.owner_id != actor_id:
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
                        generated = generate_linkedin_draft(keyword, owner=actor)
                        if generated.content_topic_id is None:
                            raise DraftGenerationError("The selected topic was not saved.")
                        selected = _selected_topic(generated.content_topic_id, generated.model)
                    else:
                        selected = _selected_topic(selected_topic_id, post.ai_model)
                        generated = generate_linkedin_post(selected, owner=actor)
                else:
                    if selected is None:
                        raise DraftGenerationError("The selected topic is unavailable.")
                    generated = generate_linkedin_post(
                        selected, owner=actor, review_guidance=guidance
                    )
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
                    owner=actor,
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
        if use_cache_lock and cache.get(lock_key) == task_id:
            cache.delete(lock_key)


@shared_task(bind=True, name="posts.regenerate_saved_topic", max_retries=0)
def regenerate_saved_topic(self: Any, post_id: int, actor_id: int) -> str:
    """Run one owner-requested regeneration against the same stored topic."""
    lock_key = f"post-generation:{post_id}"
    task_id = str(getattr(self.request, "id", "regeneration"))
    if not cache.add(lock_key, task_id, timeout=360):
        return "generation_already_running"
    User = get_user_model()
    actor = User.objects.filter(pk=actor_id, is_active=True).first()
    try:
        post = LinkedInPost.objects.select_related("content_topic__pillar").get(pk=post_id)
        if (
            post.status != PostStatus.GENERATING
            or post.content_topic_id is None
            or post.owner_id != actor_id
        ):
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
            generated = generate_linkedin_post(
                selected, owner=actor, review_guidance=post.ai_review_notes
            )
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
                owner=actor,
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
    """Queue two owner-review drafts using distinct topics from the owner's pool."""
    return run_morning_drafts(synchronous=False)


def run_morning_drafts(*, synchronous: bool, owner: Any = None) -> str:
    """Create each active user's two daily drafts, with or without Celery/Redis.

    The synchronous path is used by the local management command and invokes the
    same generation task body directly while bypassing only its Redis lock.
    """
    User = get_user_model()
    owners = [owner] if owner is not None else User.objects.filter(is_active=True).iterator()
    outcomes = [
        _run_morning_drafts_for_owner(user, synchronous=synchronous)
        for user in owners
        if user.is_active and MorningTopic.objects.filter(owner=user).exists()
    ]
    if not outcomes:
        return "completed_0_failed_0_no_configured_topic_pools"
    completed = failed = already_handled = 0
    for outcome in outcomes:
        try:
            completed += int(outcome.split("completed_", 1)[1].split("_", 1)[0])
            failed += int(outcome.split("failed_", 1)[1].split("_", 1)[0])
            already_handled += int(outcome.split("already_handled_", 1)[1])
        except (IndexError, ValueError):
            continue
    return (
        f"completed_{completed}_failed_{failed}_already_handled_{already_handled}"
    )


def _run_morning_drafts_for_owner(owner: Any, *, synchronous: bool) -> str:
    today = timezone.localdate()
    completed = 0
    failed = 0
    for run_number in range(1, MORNING_DRAFT_COUNT + 1):
        with suppress(IntegrityError):
            ScheduledDraftRun.objects.get_or_create(
                owner=owner, run_date=today, run_number=run_number
            )

    # Lock today's slots and attach placeholder posts before releasing the
    # transaction. A concurrent Beat/manual invocation will then see the slots
    # as already claimed and won't create duplicate drafts.
    claimed: list[tuple[ScheduledDraftRun, str, LinkedInPost]] = []
    already_handled = 0
    with transaction.atomic():
        runs = list(
            ScheduledDraftRun.objects.select_for_update()
            .filter(owner=owner, run_date=today, run_number__lte=MORNING_DRAFT_COUNT)
            .order_by("run_number")
        )
        active_runs = [
            run
            for run in runs
            if (run.status == ScheduledDraftRun.Status.SUCCEEDED and run.post_id)
            or (run.status == ScheduledDraftRun.Status.STARTED and run.post_id)
        ]
        already_handled = len(active_runs)
        used_topics = {
            run.post.source_keyword.casefold()
            for run in active_runs
            if run.post is not None and run.post.source_keyword
        }
        pending_runs = [
            run
            for run in runs
            if run.status == ScheduledDraftRun.Status.FAILED
            or (run.status == ScheduledDraftRun.Status.SUCCEEDED and not run.post_id)
            or (run.status == ScheduledDraftRun.Status.STARTED and not run.post_id)
        ]
        if not pending_runs:
            return f"completed_0_failed_0_already_handled_{already_handled}"

        available = list(
            MorningTopic.objects.filter(owner=owner).order_by("pk").values_list("topic", flat=True)
        )
        available = [topic for topic in available if topic.casefold() not in used_topics]
        if len(available) < len(pending_runs):
            reason = (
                "Add at least two different morning topics, or more topics if today's drafts have "
                "already used entries from the pool."
            )
            for run in pending_runs:
                _finish_run(run, ScheduledDraftRun.Status.FAILED, reason, post=run.post)
            return f"completed_0_failed_{len(pending_runs)}_already_handled_{already_handled}"

        chosen_topics = random.SystemRandom().sample(available, k=len(pending_runs))
        for run, keyword in zip(pending_runs, chosen_topics, strict=True):
            run.status = ScheduledDraftRun.Status.STARTED
            run.error_message = ""
            run.completed_at = None
            run.started_at = timezone.now()
            post = LinkedInPost.objects.create(
                owner=owner,
                origin=PostOrigin.AUTO,
                topic=keyword,
                source_keyword=keyword,
                status=PostStatus.DRAFT,
            )
            post = transition_post(post, PostStatus.GENERATING)
            run.post = post
            run.save(
                update_fields=[
                    "status",
                    "error_message",
                    "completed_at",
                    "started_at",
                    "post",
                ]
            )
            claimed.append((run, keyword, post))

    for run, _keyword, post in claimed:
        try:
            if synchronous:
                result = generate_keyword_post.run(
                    post.pk,
                    owner.pk,
                    scheduled_run_id=run.pk,
                    use_cache_lock=False,
                )
                if result == "draft_ready_for_owner_review":
                    completed += 1
                else:
                    failed += 1
            else:
                generate_keyword_post.delay(post.pk, owner.pk, scheduled_run_id=run.pk)
                completed += 1
        except Exception as exc:
            reason = (
                str(exc)
                if isinstance(exc, DraftGenerationError)
                else "The scheduled draft could not be prepared. Check the database and provider."
            )
            if post and post.status in {PostStatus.GENERATING, PostStatus.REVIEWING}:
                transition_post(post, PostStatus.FAILED, reason=reason)
            _finish_run(run, ScheduledDraftRun.Status.FAILED, reason, post=post)
            failed += 1

    return f"completed_{completed}_failed_{failed}_already_handled_{already_handled}"


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
    run = ScheduledDraftRun.objects.filter(pk=run_id, owner=post.owner).first()
    if run:
        _finish_run(run, status, error_message, post=post)
