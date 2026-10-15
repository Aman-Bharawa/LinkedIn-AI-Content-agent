from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # Django's built-in admin lives here so /admin/ stays free for the owner dashboard.
    path("django-admin/", admin.site.urls),
    path("posts/", include("apps.posts.urls")),
    path("", include("apps.core.urls")),
]
