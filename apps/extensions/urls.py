from django.urls import path

from . import views

app_name = "extensions"

urlpatterns = [
    path("pair/", views.pair_page, name="pair"),
    path("api/token/", views.exchange_pairing_code, name="token"),
    path("api/drafts/", views.drafts_api, name="drafts"),
    path("api/drafts/list/", views.drafts_list_api, name="drafts-list"),
    path("api/drafts/<int:post_id>/approve/", views.approve_api, name="approve"),
    path("api/revoke/", views.revoke_api, name="revoke"),
]
