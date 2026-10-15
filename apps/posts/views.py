from collections.abc import Callable
from functools import wraps
from typing import Any, cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.models import is_owner
from apps.ai.services import (
    DraftGenerationError,
    SelectedTopic,
    generate_linkedin_draft,
    generate_linkedin_post,
    review_linkedin_post,
)
from apps.content.models import ContentPillar, ContentTopic
from apps.core.models import AuditAction, AuditEvent

from .forms import GeneratePostForm, PostEditForm
from .models import (
    MAX_GENERATION_ATTEMPTS,
    LinkedInPost,
    PostOrigin,
    PostReview,
    PostStatus,
    PostVersion,
    VersionKind,
)
from .state import InvalidTransition, transition_post

EDITABLE_STATUSES = (PostStatus.PENDING_APPROVAL, PostStatus.NEEDS_MANUAL_REVIEW)
QUEUE_STATUSES = (PostStatus.PENDING_APPROVAL, PostStatus.NEEDS_MANUAL_REVIEW)


def owner_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    @wraps(view)
    @login_required
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not is_owner(request.user):
            return HttpResponseForbidden("Owner access required.")
        return view(request, *args, **kwargs)

    return wrapped


@owner_required
@require_POST
def generate_post(request: HttpRequest) -> HttpResponse:
    form = GeneratePostForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a keyword with at least two characters.")
        return redirect("core:home")

    keyword = form.cleaned_data["keyword"].strip()
    post = LinkedInPost.objects.create(
        topic=keyword,
        source_keyword=keyword,
        origin=PostOrigin.KEYWORD,
        status=PostStatus.DRAFT,
    )
    post = transition_post(post, PostStatus.GENERATING, actor=request.user)

    selected_topic: SelectedTopic | None = None
    review_guidance = ""
    passed_ai_review = False
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        try:
            if attempt == 1:
                generated = generate_linkedin_draft(keyword)
                if generated.content_topic_id is None:
                    raise DraftGenerationError("The generated draft has no saved topic record.")
                selected_row = ContentTopic.objects.select_related("pillar").get(
                    pk=generated.content_topic_id
                )
                selected_topic = SelectedTopic(
                    title=selected_row.title,
                    pillar_slug=selected_row.pillar.slug,
                    angle=selected_row.angle,
                    post_format=selected_row.post_format,
                    topic_id=selected_row.pk,
                    model=generated.model,
                )
            else:
                if selected_topic is None:
                    raise DraftGenerationError(
                        "The selected topic is unavailable for regeneration."
                    )
                generated = generate_linkedin_post(selected_topic, review_guidance=review_guidance)
        except (DraftGenerationError, ContentTopic.DoesNotExist) as exc:
            transition_post(post, PostStatus.FAILED, actor=request.user, reason=str(exc))
            messages.error(request, str(exc) or "The selected topic is no longer available.")
            return redirect("core:home")

        version_kind = VersionKind.AI_GENERATED if attempt == 1 else VersionKind.AI_REGENERATED
        with transaction.atomic():
            post = LinkedInPost.objects.select_for_update().get(pk=post.pk)
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
                kind=version_kind,
                topic=post.topic,
                hook=post.hook,
                content=post.content,
                hashtags=post.hashtags,
                ai_model=post.ai_model,
                prompt_version=post.prompt_version,
            )
        post = transition_post(post, PostStatus.REVIEWING, actor=request.user)
        try:
            review = review_linkedin_post(
                topic=generated.topic,
                pillar=generated.pillar_slug,
                hook=generated.hook,
                content=generated.content,
                hashtags=generated.hashtags,
            )
        except DraftGenerationError as exc:
            transition_post(post, PostStatus.FAILED, actor=request.user, reason=str(exc))
            messages.error(request, str(exc))
            return redirect("posts:detail", post_id=post.pk)

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
        if review.approved_by_ai:
            passed_ai_review = True
            post = transition_post(post, PostStatus.NEEDS_MANUAL_REVIEW, actor=request.user)
            break
        if attempt == MAX_GENERATION_ATTEMPTS:
            summary = review.feedback or "; ".join(review.problems) or "Quality thresholds not met."
            post = transition_post(
                post,
                PostStatus.NEEDS_MANUAL_REVIEW,
                actor=request.user,
                reason=summary,
            )
            break
        review_guidance = "\n".join(
            [review.feedback, *review.problems, *review.improvement_suggestions]
        )
        post = transition_post(
            post,
            PostStatus.GENERATING,
            actor=request.user,
            reason=f"AI review failed on attempt {attempt}; regenerating within the attempt limit.",
        )

    messages.success(
        request,
        "AI review passed; owner review is still required."
        if passed_ai_review
        else "Draft needs your manual review. See the saved AI feedback before approving.",
    )
    return redirect("posts:detail", post_id=post.pk)


@owner_required
@require_http_methods(["GET", "POST"])
def edit_post(request: HttpRequest, post_id: int) -> HttpResponse:
    post = get_object_or_404(LinkedInPost, pk=post_id)
    if post.status not in EDITABLE_STATUSES:
        messages.error(request, "This post can no longer be edited in its current state.")
        return redirect("posts:detail", post_id=post.pk)

    initial = {
        "topic": post.topic,
        "hook": post.hook,
        "content": post.content,
        "hashtags": " ".join(f"#{tag}" for tag in post.hashtags),
    }
    form = PostEditForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            post = LinkedInPost.objects.select_for_update().get(pk=post.pk)
            if post.status not in EDITABLE_STATUSES:
                messages.error(request, "This post changed state and can no longer be edited.")
                return redirect("posts:detail", post_id=post.pk)
            post.topic = form.cleaned_data["topic"].strip()
            post.hook = form.cleaned_data["hook"].strip()
            post.content = form.cleaned_data["content"].strip()
            post.hashtags = form.cleaned_data["hashtags"]
            post.is_manually_edited = True
            post.save()
            PostVersion.objects.create(
                post=post,
                kind=VersionKind.OWNER_EDIT,
                topic=post.topic,
                hook=post.hook,
                content=post.content,
                hashtags=post.hashtags,
                created_by=cast(Any, request.user),
            )
            AuditEvent.objects.create(
                actor=cast(Any, request.user),
                action=AuditAction.EDIT,
                post=post,
                from_status=post.status,
                to_status=post.status,
            )
        messages.success(request, "Your edits are saved. The original AI draft remains in history.")
        return redirect("posts:detail", post_id=post.pk)

    return render(request, "posts/edit.html", {"post": post, "form": form})


@owner_required
@require_GET
def post_detail(request: HttpRequest, post_id: int) -> HttpResponse:
    post = get_object_or_404(
        LinkedInPost.objects.select_related("pillar", "approved_by"), pk=post_id
    )
    return render(
        request,
        "posts/detail.html",
        {
            "post": post,
            "versions": post.versions.all(),
            "reviews": post.reviews.all(),
            "can_edit": post.status in EDITABLE_STATUSES,
            "can_review": post.status in QUEUE_STATUSES,
        },
    )


@owner_required
@require_POST
def approve_post(request: HttpRequest, post_id: int) -> HttpResponse:
    post = get_object_or_404(LinkedInPost, pk=post_id)
    try:
        post = transition_post(post, PostStatus.APPROVED, actor=request.user)
    except InvalidTransition:
        messages.error(request, "This post is no longer waiting for owner approval.")
        return redirect("posts:detail", post_id=post.pk)
    messages.success(request, "Post approved. Publishing remains a separate manual step.")
    return redirect("posts:detail", post_id=post.pk)


@owner_required
@require_POST
def reject_post(request: HttpRequest, post_id: int) -> HttpResponse:
    post = get_object_or_404(LinkedInPost, pk=post_id)
    reason = request.POST.get("reason", "").strip()[:2000]
    try:
        post = transition_post(post, PostStatus.REJECTED, actor=request.user, reason=reason)
    except InvalidTransition:
        messages.error(request, "This post is no longer waiting for owner review.")
        return redirect("posts:detail", post_id=post.pk)
    messages.info(
        request, "Post rejected and removed from the approval queue; it remains in history."
    )
    return redirect("core:home")
