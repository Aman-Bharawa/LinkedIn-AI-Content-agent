from django import forms


class MorningTopicForm(forms.Form):
    topic = forms.CharField(
        label="Add a topic",
        max_length=255,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. Building reliable AI products",
                "spellcheck": "true",
                "autocomplete": "off",
            }
        ),
    )

    def clean_topic(self) -> str:
        return " ".join(self.cleaned_data["topic"].split())
