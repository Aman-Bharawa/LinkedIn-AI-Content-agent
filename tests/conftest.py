from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import LinkedInAccount
from apps.content.models import ContentPillar
from apps.posts.models import LinkedInPost

ACCESS_TOKEN = "AQX-test-access-token-value-1234567890"
REFRESH_TOKEN = "AQX-test-refresh-token-value-0987654321"


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_superuser(
        username="owner", email="owner@example.com", password="unused-in-tests-1234"
    )


@pytest.fixture
def staff_user(django_user_model):
    return django_user_model.objects.create_user(
        username="staff", password="unused-in-tests-1234", is_staff=True
    )


@pytest.fixture
def pillar(db):
    return ContentPillar.objects.get(slug="founder-lessons")


@pytest.fixture
def post(pillar, owner):
    return LinkedInPost.objects.create(
        owner=owner,
        topic="Shipping small",
        pillar=pillar,
        hook="Most MVPs are too big.",
        content="Cut scope until it hurts, then cut once more.",
        hashtags=["startups", "#product"],
        status=LinkedInPost.Status.PENDING_APPROVAL,
    )


@pytest.fixture
def account(db, owner):
    return LinkedInAccount.objects.create(
        owner=owner,
        member_urn="urn:li:person:abc123",
        access_token=ACCESS_TOKEN,
        refresh_token=REFRESH_TOKEN,
        access_token_expires_at=timezone.now() + timedelta(days=60),
        scopes=["openid", "w_member_social"],
    )
