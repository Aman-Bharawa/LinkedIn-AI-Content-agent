from django.contrib import admin

from .models import ContentKnowledge, ContentPillar, ContentTopic, WritingExample


@admin.register(ContentPillar)
class ContentPillarAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active")
    list_filter = ("is_active",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ContentTopic)
class ContentTopicAdmin(admin.ModelAdmin):
    list_display = ("title", "pillar", "used_count", "last_used_at", "is_active")
    list_filter = ("is_active", "pillar")
    search_fields = ("title", "angle")
    readonly_fields = ("normalized_title", "used_count", "last_used_at")


@admin.register(ContentKnowledge)
class ContentKnowledgeAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "is_verified", "is_active", "source")
    list_filter = ("category", "is_verified", "is_active")
    search_fields = ("title", "content", "source")
    readonly_fields = ("verified_at",)


@admin.register(WritingExample)
class WritingExampleAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "is_owner_approved", "is_active")
    list_filter = ("is_owner_approved", "is_active")
    search_fields = ("title", "content")
