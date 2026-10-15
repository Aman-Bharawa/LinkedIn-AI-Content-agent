from decimal import Decimal

import pytest

from apps.content.models import ContentGroup, ContentPillar
from apps.content.strategy import (
    InvalidContentStrategy,
    get_pillar_allocations,
    validate_content_strategy,
)


@pytest.mark.django_db
def test_migration_seeds_five_groups_and_thirteen_pillars():
    assert ContentGroup.objects.count() == 5
    assert ContentPillar.objects.count() == 13
    assert sum(
        ContentGroup.objects.filter(is_active=True).values_list("target_share", flat=True),
        Decimal("0"),
    ) == Decimal("100.00")
    validate_content_strategy()


@pytest.mark.django_db
def test_allocations_divide_group_share_across_active_pillars():
    allocations = get_pillar_allocations()
    assert len(allocations) == 13
    assert sum(allocations.values(), Decimal("0")) == Decimal("100")
    assert all(
        allocations[pillar.slug] == pillar.target_share
        for pillar in ContentPillar.objects.filter(is_active=True).select_related("group")
    )

    ai_pillar = ContentPillar.objects.get(slug="ai-and-software-development")
    assert allocations[ai_pillar.slug] == Decimal("25.00")

    sibling = ContentPillar.objects.get(slug="developer-productivity")
    original = allocations[sibling.slug]
    sibling.is_active = False
    sibling.save(update_fields=["is_active", "updated_at"])
    adjusted = get_pillar_allocations()
    assert sibling.slug not in adjusted
    assert adjusted["engineering-leadership"] > original
    assert sum(adjusted.values(), Decimal("0")) == Decimal("100")


@pytest.mark.django_db
def test_invalid_group_total_is_rejected():
    ContentGroup.objects.filter(slug="company").update(target_share=Decimal("9.00"))
    with pytest.raises(InvalidContentStrategy, match="expected 100%"):
        get_pillar_allocations()


@pytest.mark.django_db
def test_active_pillar_cannot_be_inactive_group():
    ContentGroup.objects.filter(slug="company").update(is_active=False)
    ContentGroup.objects.filter(slug="ai-technology").update(target_share=Decimal("35.00"))
    with pytest.raises(InvalidContentStrategy, match="belong to an active group"):
        validate_content_strategy()
