from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.accounts.models import is_owner

from .forms import OwnerKnowledgeForm
from .models import ContentKnowledge, KnowledgeCategory


def _current_form_data() -> dict[str, str]:
    rows = ContentKnowledge.objects.filter(source__startswith="owner-dashboard:", is_active=True)

    def facts(category: str) -> str:
        return "\n".join(
            f"{row.content} | {row.source.removeprefix('owner-dashboard:').strip()}"
            for row in rows.filter(category=category).order_by("pk")
        )

    style = rows.filter(category=KnowledgeCategory.WRITING_STYLE).order_by("-updated_at").first()
    custom_instructions = (
        rows.filter(category=KnowledgeCategory.OTHER).order_by("-updated_at").first()
    )
    return {
        "founder_facts": facts(KnowledgeCategory.FOUNDER),
        "company_facts": facts(KnowledgeCategory.COMPANY),
        "writing_style": style.content if style else "",
        "custom_instructions": custom_instructions.content if custom_instructions else "",
        "subject_areas": "\n".join(
            rows.filter(category=KnowledgeCategory.SUBJECT_AREA)
            .order_by("pk")
            .values_list("content", flat=True)
        ),
    }


@login_required
@require_http_methods(["GET", "POST"])
def knowledge_settings(request: HttpRequest) -> HttpResponse:
    if not is_owner(request.user):
        return HttpResponseForbidden("Owner access required.")

    if request.method == "POST":
        form = OwnerKnowledgeForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Knowledge saved. Non-empty sections were updated; blank sections kept their "
                "most recently saved values. Gemini will use the updated context on its next task.",
            )
            return redirect("content:knowledge")
    else:
        form = OwnerKnowledgeForm(initial=_current_form_data())

    return render(request, "content/knowledge.html", {"form": form})
