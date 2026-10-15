from django import forms
from django.core.exceptions import ValidationError

from .models import normalize_hashtags


class GeneratePostForm(forms.Form):
    keyword = forms.CharField(
        max_length=255,
        min_length=2,
        label="What should this post be about?",
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "e.g. why startup teams should ship smaller releases",
                "autofocus": True,
            }
        ),
    )


class PostEditForm(forms.Form):
    topic = forms.CharField(max_length=255, widget=forms.TextInput(attrs={"class": "form-control"}))
    hook = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )
    content = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 12}),
    )
    hashtags = forms.CharField(
        required=False,
        label="Hashtags (separate with commas or spaces)",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "#product #engineering"}
        ),
    )

    def clean_hashtags(self) -> list[str]:
        value = self.cleaned_data["hashtags"]
        try:
            return normalize_hashtags(value.replace(",", " ").split())
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
