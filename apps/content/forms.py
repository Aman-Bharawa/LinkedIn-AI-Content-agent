from django import forms

from .models import ContentKnowledge, KnowledgeCategory


class OwnerKnowledgeForm(forms.Form):
    founder_facts = forms.CharField(
        label="Founder facts",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "class": "form-control",
                "placeholder": (
                    "Fact | source\nExample: Three years in software engineering | Owner-provided"
                ),
            }
        ),
        help_text=(
            "One fact per line, formatted as: fact | source. A source is required so factual "
            "claims are grounded."
        ),
    )
    company_facts = forms.CharField(
        label="Company facts",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "class": "form-control",
                "placeholder": (
                    "Fact | source\nExample: We build tools for independent creators. "
                    "| Owner-provided"
                ),
            }
        ),
        help_text="One fact per line, formatted as: fact | source.",
    )
    writing_style = forms.CharField(
        label="Writing style and rules",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 10,
                "class": "form-control",
                "placeholder": (
                    "Describe the voice, tone, structure, and writing rules you want the agent "
                    "to follow."
                ),
            }
        ),
        help_text=(
            "This guidance is stored as verified, owner-provided writing guidance and used when "
            "selecting topics, writing drafts, and reviewing them."
        ),
    )
    custom_instructions = forms.CharField(
        label="Custom instructions for Gemini",
        required=False,
        max_length=4000,
        widget=forms.Textarea(
            attrs={
                "rows": 7,
                "class": "form-control",
                "placeholder": (
                    "Example: Write for early-career software developers. Use concrete examples, "
                    "avoid clickbait, and end with one thoughtful question when it fits."
                ),
            }
        ),
        help_text=(
            "Add preferences for topics, audience, structure, or wording. These are included "
            "when Gemini selects topics, suggests ideas, writes drafts, and reviews drafts. "
            "They guide the output but do not make unsupported facts true."
        ),
    )
    subject_areas = forms.CharField(
        label="Approved subject areas",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 5,
                "class": "form-control",
                "placeholder": "AI in software development\nC++ systems programming",
            }
        ),
        help_text="One approved subject area per line.",
    )

    def clean_founder_facts(self) -> list[tuple[str, str]]:
        return self._parse_facts(self.cleaned_data["founder_facts"], "Founder facts")

    def clean_company_facts(self) -> list[tuple[str, str]]:
        return self._parse_facts(self.cleaned_data["company_facts"], "Company facts")

    @staticmethod
    def _parse_facts(value: str, label: str) -> list[tuple[str, str]]:
        facts = []
        for line_number, line in enumerate(value.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            fact, separator, source = line.partition("|")
            fact, source = fact.strip(), source.strip()
            if not separator or not fact or not source:
                raise forms.ValidationError(
                    f"{label}, line {line_number}: use 'fact | source' and provide both parts."
                )
            facts.append((fact, source))
        return facts

    def clean_subject_areas(self) -> list[str]:
        return [
            line.strip() for line in self.cleaned_data["subject_areas"].splitlines() if line.strip()
        ]

    def save(self) -> None:
        if not self.is_valid():
            raise ValueError("Call save() only after the form has validated.")

        from django.db import transaction

        with transaction.atomic():
            founder_facts = self.cleaned_data["founder_facts"]
            if founder_facts:
                self._deactivate_dashboard_values(KnowledgeCategory.FOUNDER)
                self._save_facts(KnowledgeCategory.FOUNDER, founder_facts)

            company_facts = self.cleaned_data["company_facts"]
            if company_facts:
                self._deactivate_dashboard_values(KnowledgeCategory.COMPANY)
                self._save_facts(KnowledgeCategory.COMPANY, company_facts)

            style = self.cleaned_data["writing_style"].strip()
            if style:
                self._deactivate_dashboard_values(KnowledgeCategory.WRITING_STYLE)
                ContentKnowledge.objects.create(
                    category=KnowledgeCategory.WRITING_STYLE,
                    title="Owner writing style",
                    content=style,
                    source="owner-dashboard: owner-provided",
                    is_verified=True,
                    is_active=True,
                )

            custom_instructions = self.cleaned_data["custom_instructions"].strip()
            if custom_instructions:
                self._deactivate_dashboard_values(KnowledgeCategory.OTHER)
                ContentKnowledge.objects.create(
                    category=KnowledgeCategory.OTHER,
                    title="Owner custom instructions",
                    content=custom_instructions,
                    source="owner-dashboard: custom instructions",
                    is_verified=True,
                    is_active=True,
                )

            subject_areas = self.cleaned_data["subject_areas"]
            if subject_areas:
                self._deactivate_dashboard_values(KnowledgeCategory.SUBJECT_AREA)
                for area in subject_areas:
                    ContentKnowledge.objects.create(
                        category=KnowledgeCategory.SUBJECT_AREA,
                        title=area[:255],
                        content=area,
                        source="owner-dashboard: owner-provided",
                        is_verified=True,
                        is_active=True,
                    )

    @staticmethod
    def _deactivate_dashboard_values(category: str) -> None:
        ContentKnowledge.objects.filter(
            category=category, source__startswith="owner-dashboard:", is_active=True
        ).update(is_active=False)

    @staticmethod
    def _save_facts(category: str, facts: list[tuple[str, str]]) -> None:
        for fact, source in facts:
            ContentKnowledge.objects.create(
                category=category,
                title=fact[:255],
                content=fact,
                source=f"owner-dashboard: {source}"[:500],
                is_verified=True,
                is_active=True,
            )
