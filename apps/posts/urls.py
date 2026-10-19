from django.urls import path

from . import views

app_name = "posts"

urlpatterns = [
    path("generate/", views.generate_post, name="generate"),
    path("<int:post_id>/regenerate/", views.regenerate_post, name="regenerate"),
    path("<int:post_id>/", views.post_detail, name="detail"),
    path("<int:post_id>/edit/", views.edit_post, name="edit"),
    path("<int:post_id>/approve/", views.approve_post, name="approve"),
    path("<int:post_id>/schedule/", views.schedule_post, name="schedule"),
    path("<int:post_id>/cancel-schedule/", views.cancel_schedule, name="cancel-schedule"),
    path("<int:post_id>/reject/", views.reject_post, name="reject"),
]
