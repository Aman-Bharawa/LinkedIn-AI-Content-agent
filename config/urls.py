from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("posts/", include("apps.posts.urls")),
    path("linkedin/", include("apps.linkedin.urls")),
    path("knowledge/", include("apps.content.urls")),
    path("extension/", include("apps.extensions.urls")),
    path("", include("apps.core.urls")),
]
