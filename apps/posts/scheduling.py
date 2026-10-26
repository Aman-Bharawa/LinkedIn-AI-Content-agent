from __future__ import annotations

import secrets
from datetime import date, datetime, time, timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from .models import (
    ACTIVE_PUBLICATION_STATUSES,
    LinkedInPost,
    PostPublication,
    PostStatus,
    PublicationScheduleSettings,
)


def _random_publish_time(
    day: date, settings: PublicationScheduleSettings, now: datetime
) -> datetime | None:
    start_minute = settings.window_start.hour * 60 + settings.window_start.minute
    end_minute = settings.window_end.hour * 60 + settings.window_end.minute
    if day == timezone.localtime(now).date():
        local_now = timezone.localtime(now)
        first_available = local_now.hour * 60 + local_now.minute + 1
        start_minute = max(start_minute, first_available)
    if start_minute > end_minute:
        return None

    minute = start_minute + secrets.randbelow(end_minute - start_minute + 1)
    local_time = time(hour=minute // 60, minute=minute % 60)
    return timezone.make_aware(datetime.combine(day, local_time), timezone.get_current_timezone())


def _within_window(value: datetime, settings: PublicationScheduleSettings) -> bool:
    local_value = timezone.localtime(value)
    return (
        settings.window_start
        <= local_value.time().replace(second=0, microsecond=0)
        <= settings.window_end
    )


@transaction.atomic
def refresh_publication_queue(*, owner: Any) -> int:
    """Keep one user's approved posts in order, at most one assigned per local day."""
    schedule = PublicationScheduleSettings.get_for_user(owner)
    now = timezone.now()
    today = timezone.localtime(now).date()

    dispatched_posts = LinkedInPost.objects.filter(
        owner=owner,
        status=PostStatus.APPROVED,
        publication_queued_at__isnull=False,
        scheduled_for__isnull=False,
    )
    occupied_days = {
        timezone.localtime(value).date()
        for value in dispatched_posts.values_list("scheduled_for", flat=True)
    }
    if dispatched_posts.filter(scheduled_for__lte=now).exists():
        # A broker message can outlive its assigned day. Keep today's slot
        # reserved until that already-dispatched post is handled.
        occupied_days.add(today)
    # A post that reached LinkedIn, or started a publication attempt today,
    # consumes today's slot even after its status changes from Approved.
    occupied_days.update(
        timezone.localtime(value).date()
        for value in LinkedInPost.objects.filter(
            owner=owner, published_at__date=today
        ).values_list(
            "published_at", flat=True
        )
    )
    occupied_days.update(
        timezone.localtime(value).date()
        for value in PostPublication.objects.filter(
            post__owner=owner, started_at__date=today
        ).values_list(
            "started_at", flat=True
        )
    )
    if PostPublication.objects.filter(
        post__owner=owner, status__in=ACTIVE_PUBLICATION_STATUSES
    ).exists():
        occupied_days.add(today)

    posts = list(
        LinkedInPost.objects.select_for_update()
        .filter(
            owner=owner,
            status=PostStatus.APPROVED,
            publication_queued_at__isnull=True,
            approved_by__is_active=True,
            pillar__isnull=False,
        )
        .exclude(content="")
        .order_by("approved_at", "pk")
    )

    changed = 0
    target_day = today
    for post in posts:
        while target_day in occupied_days:
            target_day += timedelta(days=1)
        current = post.scheduled_for
        current_is_in_target_slot = (
            current is not None
            and timezone.localtime(current).date() == target_day
            and _within_window(current, schedule)
        )
        assigned = (
            current
            if current_is_in_target_slot
            else _random_publish_time(target_day, schedule, now)
        )
        while assigned is None:
            target_day += timedelta(days=1)
            while target_day in occupied_days:
                target_day += timedelta(days=1)
            assigned = _random_publish_time(target_day, schedule, now)

        if current != assigned:
            post.scheduled_for = assigned
            post.save(update_fields=["scheduled_for", "updated_at"])
            changed += 1
        occupied_days.add(target_day)
        target_day += timedelta(days=1)
    return changed
