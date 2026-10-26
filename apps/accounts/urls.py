from django.contrib.auth import views as auth_views
from django.urls import path

from .forms import SignInForm
from . import views

app_name = "accounts"

urlpatterns = [
    path("sign-up/", views.sign_up, name="sign-up"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html", authentication_form=SignInForm
        ),
        name="login",
    ),
    path(
        "logout/",
        auth_views.LogoutView.as_view(),
        name="logout",
    ),
]
