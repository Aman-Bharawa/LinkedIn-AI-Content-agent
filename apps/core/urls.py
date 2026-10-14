from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.liveness, name="health"),
    path("health/ready/", views.readiness, name="health-ready"),
]
