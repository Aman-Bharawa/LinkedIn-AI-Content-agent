from django.urls import path

from . import views

app_name = "linkedin"

urlpatterns = [
    path("connect/", views.connect, name="connect"),
    path("callback/", views.callback, name="callback"),
    path("disconnect/", views.disconnect, name="disconnect"),
    path("posts/<int:post_id>/publish/", views.publish, name="publish"),
]
