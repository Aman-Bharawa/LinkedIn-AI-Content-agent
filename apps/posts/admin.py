from django.contrib import admin
from django.http import HttpRequest

from .models import LinkedInPost, PostPublication, PostReview, PostVersion


class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False


class PostVersionInline(ReadOnlyInline):
    model = PostVersion
    fields = ("version_number", "kind", "created_by", "created_at")


class PostReviewInline(ReadOnlyInline):
    model = PostReview
    fields = ("attempt_number", "overall_score", "approved_by_ai", "has_unsupported_claims")


class PostPublicationInline(ReadOnlyInline):
    model = PostPublication
    fields = ("status", "http_status", "linkedin_post_urn", "started_at", "completed_at")


@admin.register(LinkedInPost)
class LinkedInPostAdmin(admin.ModelAdmin):
    """Inspection view. Status changes go through the owner dashboard (Milestone 7)."""

    list_display = ("__str__", "status", "pillar", "ai_review_score", "created_at")
    list_filter = ("status", "pillar")
    search_fields = ("topic", "hook", "content")
    inlines = [PostVersionInline, PostReviewInline, PostPublicationInline]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False


@admin.register(PostReview)
class PostReviewAdmin(admin.ModelAdmin):
    list_display = ("post", "attempt_number", "overall_score", "approved_by_ai", "reviewed_at")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False


@admin.register(PostVersion)
class PostVersionAdmin(admin.ModelAdmin):
    list_display = ("post", "version_number", "kind", "created_at")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False


@admin.register(PostPublication)
class PostPublicationAdmin(admin.ModelAdmin):
    list_display = ("post", "status", "http_status", "linkedin_post_urn", "created_at")
    list_filter = ("status",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False
