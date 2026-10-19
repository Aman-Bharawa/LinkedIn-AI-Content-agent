from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

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


class SchedulePublicationForm(forms.Form):
    scheduled_for = forms.DateTimeField(
        label="Publish on",
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"class": "form-control", "type": "datetime-local", "required": True},
        ),
    )

    def clean_scheduled_for(self):
        scheduled_for = self.cleaned_data["scheduled_for"]
        if scheduled_for <= timezone.now():
            raise forms.ValidationError("Choose a future date and time.")
        return scheduled_for


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
