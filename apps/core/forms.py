from django import forms


class MorningTopicForm(forms.Form):
    topics = forms.CharField(
        label="Topics for morning drafts",
        max_length=12_799,
        widget=forms.Textarea(
            attrs={
                "rows": 12,
                "class": "form-control",
                "placeholder": (
                    "AI in software development\nBuilding reliable systems\nC++ performance tips"
                ),
                "spellcheck": "true",
            }
        ),
        help_text=(
            "Enter one topic per line. At 8:00 a.m., the run randomly selects two different topics "
            "as generation keywords. Save at least two topics; ten is a good pool."
        ),
    )

    def clean_topics(self) -> list[str]:
        value = self.cleaned_data["topics"]
        topics: list[str] = []
        seen: set[str] = set()
        for line_number, line in enumerate(value.splitlines(), start=1):
            topic = " ".join(line.split())
            if not topic:
                continue
            if len(topic) > 255:
                raise forms.ValidationError(f"Line {line_number} is longer than 255 characters.")
            normalized = topic.casefold()
            if normalized not in seen:
                topics.append(topic)
                seen.add(normalized)
        if len(topics) < 2:
            raise forms.ValidationError("Enter at least two different topics.")
        if len(topics) > 50:
            raise forms.ValidationError("Enter no more than 50 topics.")
        return topics
