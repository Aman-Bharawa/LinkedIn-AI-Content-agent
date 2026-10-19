from collections.abc import Callable
from functools import wraps
from typing import Any, cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.models import is_owner
from apps.core.models import AuditAction, AuditEvent

from .forms import GeneratePostForm, PostEditForm, SchedulePublicationForm
from .models import (
    MAX_GENERATION_ATTEMPTS,
    LinkedInPost,
    PostOrigin,
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
    try:
        from .tasks import generate_keyword_post

        generate_keyword_post.delay(post.pk, cast(Any, request.user).pk)
    except Exception:
        transition_post(
            post,
            PostStatus.FAILED,
            actor=request.user,
            reason="The background worker could not accept this generation job.",
        )
        messages.error(request, "Draft generation could not be queued. Check the worker and retry.")
    else:
        messages.success(request, "Draft generation started. Refresh this post to see its status.")
    return redirect("posts:detail", post_id=post.pk)


@owner_required
@require_POST
def regenerate_post(request: HttpRequest, post_id: int) -> HttpResponse:
    post = get_object_or_404(
        LinkedInPost.objects.select_related("content_topic__pillar"), pk=post_id
    )
    if (
        post.status not in {*EDITABLE_STATUSES, PostStatus.FAILED}
        or post.generation_attempts >= MAX_GENERATION_ATTEMPTS
        or post.content_topic is None
    ):
        messages.error(request, "This post cannot be regenerated in its current state.")
        return redirect("posts:detail", post_id=post.pk)

    post = transition_post(post, PostStatus.GENERATING, actor=request.user)
    try:
        from .tasks import regenerate_saved_topic

        regenerate_saved_topic.delay(post.pk, cast(Any, request.user).pk)
    except Exception:
        transition_post(
            post,
            PostStatus.FAILED,
            actor=request.user,
            reason="The background worker could not accept this regeneration job.",
        )
        messages.error(request, "Regeneration could not be queued. Check the worker and retry.")
    else:
        messages.success(request, "Regeneration started for the saved topic.")
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
        LinkedInPost.objects.select_related("pillar", "approved_by", "content_topic"),
        pk=post_id,
    )
    return render(
        request,
        "posts/detail.html",
        {
            "post": post,
            "versions": post.versions.all(),
            "reviews": post.reviews.all(),
            "publications": post.publications.all(),
            "can_edit": post.status in EDITABLE_STATUSES,
            "can_regenerate": (
                post.status in {*EDITABLE_STATUSES, PostStatus.FAILED}
                and post.generation_attempts < MAX_GENERATION_ATTEMPTS
                and post.content_topic_id is not None
            ),
            "can_review": post.status in QUEUE_STATUSES,
            "can_schedule": post.status == PostStatus.APPROVED,
            "schedule_form": SchedulePublicationForm(initial={"scheduled_for": post.scheduled_for}),
            "display_timezone": timezone.get_current_timezone_name(),
        },
    )


@owner_required
@require_POST
def approve_post(request: HttpRequest, post_id: int) -> HttpResponse:
    post = get_object_or_404(LinkedInPost, pk=post_id)
    form = SchedulePublicationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a valid future date and time to schedule this post.")
        return redirect("posts:detail", post_id=post.pk)
    try:
        with transaction.atomic():
            post = transition_post(post, PostStatus.APPROVED, actor=request.user)
            post.scheduled_for = form.cleaned_data["scheduled_for"]
            post.publication_queued_at = None
            post.save(update_fields=["scheduled_for", "publication_queued_at", "updated_at"])
            AuditEvent.objects.create(
                actor=request.user,
                action=AuditAction.STATUS_CHANGE,
                post=post,
                from_status=PostStatus.APPROVED,
                to_status=PostStatus.APPROVED,
                metadata={"scheduled_for": post.scheduled_for.isoformat()},
            )
    except InvalidTransition:
        messages.error(request, "This post is no longer waiting for owner approval.")
        return redirect("posts:detail", post_id=post.pk)
    messages.success(
        request,
        "Post approved and added to the automatic publishing queue.",
    )
    return redirect("posts:detail", post_id=post.pk)


@owner_required
@require_POST
def schedule_post(request: HttpRequest, post_id: int) -> HttpResponse:
    form = SchedulePublicationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a valid future date and time to schedule this post.")
        return redirect("posts:detail", post_id=post_id)
    with transaction.atomic():
        post = get_object_or_404(LinkedInPost.objects.select_for_update(), pk=post_id)
        if post.status != PostStatus.APPROVED or not post.is_publishable:
            messages.error(request, "Only an owner-approved post can be scheduled.")
            return redirect("posts:detail", post_id=post.pk)
        post.scheduled_for = form.cleaned_data["scheduled_for"]
        post.publication_queued_at = None
        post.last_error = ""
        post.save(
            update_fields=["scheduled_for", "publication_queued_at", "last_error", "updated_at"]
        )
        AuditEvent.objects.create(
            actor=request.user,
            action=AuditAction.STATUS_CHANGE,
            post=post,
            from_status=PostStatus.APPROVED,
            to_status=PostStatus.APPROVED,
            metadata={"scheduled_for": post.scheduled_for.isoformat()},
        )
    messages.success(request, "The approved post's automatic publishing time was updated.")
    return redirect("posts:detail", post_id=post.pk)


@owner_required
@require_POST
def cancel_schedule(request: HttpRequest, post_id: int) -> HttpResponse:
    with transaction.atomic():
        post = get_object_or_404(LinkedInPost.objects.select_for_update(), pk=post_id)
        if (
            post.status != PostStatus.APPROVED
            or post.scheduled_for is None
            or post.publication_queued_at is not None
        ):
            messages.error(request, "This post's schedule can no longer be cancelled.")
            return redirect("posts:detail", post_id=post.pk)
        post.scheduled_for = None
        post.save(update_fields=["scheduled_for", "updated_at"])
        AuditEvent.objects.create(
            actor=request.user,
            action=AuditAction.STATUS_CHANGE,
            post=post,
            from_status=PostStatus.APPROVED,
            to_status=PostStatus.APPROVED,
            metadata={"schedule_cancelled": True},
        )
    messages.info(request, "Automatic publishing was cancelled. The approved post stays in drafts.")
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
