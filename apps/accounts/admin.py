from django.contrib import admin
from django.http import HttpRequest

from .models import LinkedInAccount

SAFE_FIELDS = (
    "member_urn",
    "is_active",
    "scopes",
    "access_token_expires_at",
    "refresh_token_expires_at",
    "connected_at",
    "disconnected_at",
    "created_at",
    "updated_at",
)


@admin.register(LinkedInAccount)
class LinkedInAccountAdmin(admin.ModelAdmin):
    list_display = ("member_urn", "is_active", "access_token_expires_at", "connected_at")
    list_filter = ("is_active",)
    fields = SAFE_FIELDS
    readonly_fields = SAFE_FIELDS
    exclude = ("access_token", "refresh_token")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False
