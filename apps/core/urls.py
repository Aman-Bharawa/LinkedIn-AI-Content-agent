from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("admin/posts/", views.home, name="posts-dashboard"),
    path("health/", views.liveness, name="health"),
    path("health/ready/", views.readiness, name="health-ready"),
]
