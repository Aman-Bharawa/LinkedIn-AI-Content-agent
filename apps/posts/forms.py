from datetime import datetime, time

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import PublicationScheduleSettings, normalize_hashtags


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
    publish_time = forms.TimeField(
        label="Publish time",
        input_formats=["%H:%M"],
        widget=forms.TimeInput(
            format="%H:%M",
            attrs={"class": "form-control", "type": "time", "required": True},
        ),
    )

    def __init__(
        self,
        *args,
        scheduled_for: datetime,
        window_start: time,
        window_end: time,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.scheduled_for = scheduled_for
        self.window_start = window_start
        self.window_end = window_end

    def clean_publish_time(self) -> time:
        publish_time = self.cleaned_data["publish_time"]
        if not self.window_start <= publish_time <= self.window_end:
            raise forms.ValidationError("Choose a time inside the shared publishing window.")
        if self.scheduled_for is None:
            raise forms.ValidationError("This post does not have a queue day yet.")
        local_schedule = timezone.localtime(self.scheduled_for)
        scheduled_for = timezone.make_aware(
            datetime.combine(local_schedule.date(), publish_time),
            timezone.get_current_timezone(),
        )
        if scheduled_for <= timezone.now():
            raise forms.ValidationError("Choose a future time on this post's assigned day.")
        return publish_time


class PublicationWindowForm(forms.ModelForm):
    class Meta:
        model = PublicationScheduleSettings
        fields = ("window_start", "window_end")
        widgets = {
            "window_start": forms.TimeInput(
                format="%H:%M",
                attrs={"class": "form-control", "type": "time", "required": True},
            ),
            "window_end": forms.TimeInput(
                format="%H:%M",
                attrs={"class": "form-control", "type": "time", "required": True},
            ),
        }

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get("window_start")
        end = cleaned_data.get("window_end")
        if start is not None and end is not None and start >= end:
            self.add_error("window_end", "The end time must be after the start time.")
        return cleaned_data


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
