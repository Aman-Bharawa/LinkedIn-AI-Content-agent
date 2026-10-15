"""Validation and calculated allocations for the editable content strategy."""

from decimal import Decimal

from .models import ContentGroup, ContentPillar

EXPECTED_TOTAL = Decimal("100.00")


class InvalidContentStrategy(ValueError):
    """Raised when active groups and pillars cannot define a full strategy."""


def validate_content_strategy() -> None:
    """Ensure active group weights form a complete, usable 100% strategy."""
    groups = list(ContentGroup.objects.filter(is_active=True).prefetch_related("pillars"))
    total = sum((group.target_share for group in groups), Decimal("0"))
    if total != EXPECTED_TOTAL:
        raise InvalidContentStrategy(f"Active content groups total {total}%; expected 100%.")

    for group in groups:
        if not group.pillars.filter(is_active=True).exists():
            raise InvalidContentStrategy(f"Active group {group.name!r} has no active pillars.")

    if ContentPillar.objects.filter(is_active=True, group__is_active=False).exists():
        raise InvalidContentStrategy("Every active pillar must belong to an active group.")


def get_pillar_allocations() -> dict[str, Decimal]:
    """Return each active pillar's percentage, dividing its group's share equally."""
    validate_content_strategy()
    allocations: dict[str, Decimal] = {}
    groups = ContentGroup.objects.filter(is_active=True).prefetch_related("pillars")
    for group in groups:
        pillars = list(group.pillars.filter(is_active=True).order_by("name"))
        cents = int(group.target_share * 100)
        base, remainder = divmod(cents, len(pillars))
        for index, pillar in enumerate(pillars):
            pillar_cents = base + (1 if index < remainder else 0)
            allocations[pillar.slug] = Decimal(pillar_cents) / 100
    return allocations
