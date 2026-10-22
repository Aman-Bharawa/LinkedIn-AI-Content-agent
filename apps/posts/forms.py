import secrets
from datetime import date, datetime, time, timedelta

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


class ApprovalPublicationForm(forms.Form):
    publish_date = forms.DateField(
        label="Publish date",
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date", "required": True}
        ),
    )
    window_start = forms.TimeField(
        label="Window starts",
        input_formats=["%H:%M"],
        widget=forms.TimeInput(
            format="%H:%M",
            attrs={"class": "form-control", "type": "time", "required": True},
        ),
    )
    window_end = forms.TimeField(
        label="Window ends",
        input_formats=["%H:%M"],
        widget=forms.TimeInput(
            format="%H:%M",
            attrs={"class": "form-control", "type": "time", "required": True},
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        publish_date: date | None = cleaned_data.get("publish_date")
        window_start: time | None = cleaned_data.get("window_start")
        window_end: time | None = cleaned_data.get("window_end")
        if publish_date is None or window_start is None or window_end is None:
            return cleaned_data

        if window_start > window_end:
            self.add_error("window_end", "The end of the window must be after its start.")
            return cleaned_data

        current_tz = timezone.get_current_timezone()
        start_at = timezone.make_aware(datetime.combine(publish_date, window_start), current_tz)
        end_at = timezone.make_aware(datetime.combine(publish_date, window_end), current_tz)
        now = timezone.localtime()
        if end_at <= now:
            self.add_error("window_end", "Choose a window that still has time remaining.")
            return cleaned_data

        # Select at minute precision, matching the inputs and giving Beat a full
        # minute to pick up the post when the chosen time arrives.
        if publish_date == now.date():
            next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            start_at = max(start_at, next_minute)
        first_minute = int(start_at.timestamp() // 60)
        last_minute = int(end_at.timestamp() // 60)
        if first_minute > last_minute:
            self.add_error("window_start", "There is no future publishing time left in this window.")
            return cleaned_data

        chosen_minute = first_minute + secrets.randbelow(last_minute - first_minute + 1)
        cleaned_data["scheduled_for"] = datetime.fromtimestamp(
            chosen_minute * 60, tz=current_tz
        )
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
