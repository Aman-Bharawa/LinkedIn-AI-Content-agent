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

from .forms import (
    GeneratePostForm,
    PostEditForm,
    PublicationWindowForm,
    SchedulePublicationForm,
)
from .models import (
    MAX_GENERATION_ATTEMPTS,
    LinkedInPost,
    PostOrigin,
    PostStatus,
    PostVersion,
    PublicationScheduleSettings,
    VersionKind,
)
from .scheduling import refresh_publication_queue
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
    owner = cast(Any, request.user)
    form = GeneratePostForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a keyword with at least two characters.")
        return redirect("core:home")

    keyword = form.cleaned_data["keyword"].strip()
    post = LinkedInPost.objects.create(
        owner=owner,
        topic=keyword,
        source_keyword=keyword,
        origin=PostOrigin.KEYWORD,
        status=PostStatus.DRAFT,
    )
    post = transition_post(post, PostStatus.GENERATING, actor=owner)
    try:
        from .tasks import generate_keyword_post

        generate_keyword_post.delay(post.pk, owner.pk)
    except Exception:
        transition_post(
            post,
            PostStatus.FAILED,
            actor=owner,
            reason="The background worker could not accept this generation job.",
        )
        messages.error(request, "Draft generation could not be queued. Check the worker and retry.")
    else:
        messages.success(request, "Draft generation started. Refresh this post to see its status.")
    return redirect("posts:detail", post_id=post.pk)


@owner_required
@require_POST
def regenerate_post(request: HttpRequest, post_id: int) -> HttpResponse:
    owner = cast(Any, request.user)
    post = get_object_or_404(
        LinkedInPost.objects.filter(owner=owner).select_related("content_topic__pillar"),
        pk=post_id,
    )
    if (
        post.status not in {*EDITABLE_STATUSES, PostStatus.FAILED}
        or post.generation_attempts >= MAX_GENERATION_ATTEMPTS
        or post.content_topic is None
    ):
        messages.error(request, "This post cannot be regenerated in its current state.")
        return redirect("posts:detail", post_id=post.pk)

    post = transition_post(post, PostStatus.GENERATING, actor=owner)
    try:
        from .tasks import regenerate_saved_topic

        regenerate_saved_topic.delay(post.pk, owner.pk)
    except Exception:
        transition_post(
            post,
            PostStatus.FAILED,
            actor=owner,
            reason="The background worker could not accept this regeneration job.",
        )
        messages.error(request, "Regeneration could not be queued. Check the worker and retry.")
    else:
        messages.success(request, "Regeneration started for the saved topic.")
    return redirect("posts:detail", post_id=post.pk)


@owner_required
@require_http_methods(["GET", "POST"])
def edit_post(request: HttpRequest, post_id: int) -> HttpResponse:
    owner = cast(Any, request.user)
    post = get_object_or_404(LinkedInPost, pk=post_id, owner=owner)
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
            post = LinkedInPost.objects.select_for_update().get(pk=post.pk, owner=owner)
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
    owner = cast(Any, request.user)
    post = get_object_or_404(
        LinkedInPost.objects.filter(owner=owner).select_related(
            "pillar", "approved_by", "content_topic"
        ),
        pk=post_id,
    )
    publication_settings = PublicationScheduleSettings.get_for_user(owner)
    can_schedule = (
        post.status == PostStatus.APPROVED
        and post.scheduled_for is not None
        and post.publication_queued_at is None
    )
    schedule_form = None
    if can_schedule and post.scheduled_for is not None:
        schedule_form = SchedulePublicationForm(
            initial={"publish_time": timezone.localtime(post.scheduled_for).strftime("%H:%M")},
            scheduled_for=post.scheduled_for,
            window_start=publication_settings.window_start,
            window_end=publication_settings.window_end,
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
            "can_schedule": can_schedule,
            "schedule_form": schedule_form,
            "publication_settings": publication_settings,
            "display_timezone": timezone.get_current_timezone_name(),
        },
    )


@owner_required
@require_POST
def approve_post(request: HttpRequest, post_id: int) -> HttpResponse:
    owner = cast(Any, request.user)
    post = get_object_or_404(LinkedInPost, pk=post_id, owner=owner)
    try:
        with transaction.atomic():
            post = transition_post(post, PostStatus.APPROVED, actor=request.user)
            post.publication_queued_at = None
            post.save(update_fields=["publication_queued_at", "updated_at"])
            refresh_publication_queue(owner=owner)
            post.refresh_from_db(fields=["scheduled_for"])
            AuditEvent.objects.create(
                actor=cast(Any, request.user),
                action=AuditAction.STATUS_CHANGE,
                post=post,
                from_status=PostStatus.APPROVED,
                to_status=PostStatus.APPROVED,
                metadata=(
                    {"scheduled_for": post.scheduled_for.isoformat()}
                    if post.scheduled_for is not None
                    else {}
                ),
            )
    except InvalidTransition:
        messages.error(request, "This post is no longer waiting for owner approval.")
        return redirect("posts:detail", post_id=post.pk)
    messages.success(
        request,
        "Post approved and added to the queue. The queue publishes at most one post per day.",
    )
    return redirect("posts:detail", post_id=post.pk)


@owner_required
@require_POST
def schedule_post(request: HttpRequest, post_id: int) -> HttpResponse:
    owner = cast(Any, request.user)
    with transaction.atomic():
        post = get_object_or_404(
            LinkedInPost.objects.select_for_update().filter(owner=owner), pk=post_id
        )
        if (
            post.status != PostStatus.APPROVED
            or not post.is_publishable
            or post.scheduled_for is None
            or post.publication_queued_at is not None
        ):
            messages.error(request, "Only an owner-approved post can be scheduled.")
            return redirect("posts:detail", post_id=post.pk)
        publication_settings = PublicationScheduleSettings.get_for_user(owner)
        form = SchedulePublicationForm(
            request.POST,
            scheduled_for=post.scheduled_for,
            window_start=publication_settings.window_start,
            window_end=publication_settings.window_end,
        )
        if not form.is_valid():
            messages.error(request, "Choose a future time inside the shared publishing window.")
            return redirect("posts:detail", post_id=post.pk)
        local_schedule = timezone.localtime(post.scheduled_for)
        post.scheduled_for = timezone.make_aware(
            local_schedule.replace(
                hour=form.cleaned_data["publish_time"].hour,
                minute=form.cleaned_data["publish_time"].minute,
                second=0,
                microsecond=0,
                tzinfo=None,
            ),
            timezone.get_current_timezone(),
        )
        post.last_error = ""
        post.save(update_fields=["scheduled_for", "last_error", "updated_at"])
        refresh_publication_queue(owner=owner)
        AuditEvent.objects.create(
            actor=cast(Any, request.user),
            action=AuditAction.STATUS_CHANGE,
            post=post,
            from_status=PostStatus.APPROVED,
            to_status=PostStatus.APPROVED,
            metadata={"scheduled_for": post.scheduled_for.isoformat()},
        )
    messages.success(request, "The post's time was updated within the shared daily window.")
    return redirect("posts:detail", post_id=post.pk)


@owner_required
@require_POST
def cancel_schedule(request: HttpRequest, post_id: int) -> HttpResponse:
    owner = cast(Any, request.user)
    with transaction.atomic():
        post = get_object_or_404(
            LinkedInPost.objects.select_for_update().filter(owner=owner), pk=post_id
        )
        if post.status != PostStatus.APPROVED or post.publication_queued_at is not None:
            messages.error(request, "This post can no longer be returned to review.")
            return redirect("posts:detail", post_id=post.pk)
        post = transition_post(post, PostStatus.PENDING_APPROVAL, actor=request.user)
        post.scheduled_for = None
        post.save(update_fields=["scheduled_for", "updated_at"])
        refresh_publication_queue(owner=owner)
    messages.info(request, "The post was removed from the publishing queue and returned to review.")
    return redirect("posts:detail", post_id=post.pk)


@owner_required
@require_http_methods(["GET", "POST"])
def publication_window(request: HttpRequest) -> HttpResponse:
    owner = cast(Any, request.user)
    publication_settings = PublicationScheduleSettings.get_for_user(owner)
    form = PublicationWindowForm(request.POST or None, instance=publication_settings)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
            refresh_publication_queue(owner=owner)
        messages.success(
            request,
            "Daily window saved. Approved posts were arranged one per day in queue order.",
        )
        return redirect("posts:publication-window")
    return render(
        request,
        "posts/publication_window.html",
        {
            "form": form,
            "publication_settings": publication_settings,
            "display_timezone": timezone.get_current_timezone_name(),
            "queued_posts": LinkedInPost.objects.filter(
                owner=owner,
                status=PostStatus.APPROVED,
                scheduled_for__isnull=False,
                publication_queued_at__isnull=True,
            ).order_by("scheduled_for", "pk"),
        },
    )


@owner_required
@require_POST
def reject_post(request: HttpRequest, post_id: int) -> HttpResponse:
    owner = cast(Any, request.user)
    post = get_object_or_404(LinkedInPost, pk=post_id, owner=owner)
    reason = request.POST.get("reason", "").strip()[:2000]
    try:
        post = transition_post(post, PostStatus.REJECTED, actor=owner, reason=reason)
    except InvalidTransition:
        messages.error(request, "This post is no longer waiting for owner review.")
        return redirect("posts:detail", post_id=post.pk)
    messages.info(
        request, "Post rejected and removed from the approval queue; it remains in history."
    )
    return redirect("core:home")
