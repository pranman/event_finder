"""Validated, shareable filters for the public event catalogue."""

from datetime import timedelta

from django import forms

from .models import Category, Event


class EventFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=200, label="Search", widget=forms.TextInput(attrs={
        "placeholder": "Event, venue or interest…", "type": "search", "class": "input catalogue-input",
    }))
    period = forms.ChoiceField(required=False, label="When", initial="next30", choices=[
        ("next30", "Next 30 days"), ("today", "Today"), ("tomorrow", "Tomorrow"),
        ("weekend", "This weekend"), ("next7", "Next 7 days"), ("custom", "Custom dates"),
    ], widget=forms.Select(attrs={"class": "select catalogue-input"}))
    category = forms.ModelChoiceField(
        queryset=Category.objects.all(), to_field_name="slug", required=False,
        empty_label="All categories", widget=forms.Select(attrs={"class": "select catalogue-input"}),
    )
    price = forms.ChoiceField(required=False, label="Price", choices=[
        ("", "Any price"), (Event.PriceStatus.FREE, "Free"),
        (Event.PriceStatus.PAID, "Paid"), (Event.PriceStatus.UNKNOWN, "Price not listed"),
    ], widget=forms.Select(attrs={"class": "select catalogue-input"}))

    def __init__(self, data=None, *, today, **kwargs):
        data = data.copy() if data is not None else {}
        if not data.get("period"):
            data["period"] = "next30"
        super().__init__(data=data, **kwargs)
        self.today = today
        for name, label in (("from", "From"), ("to", "To")):
            self.fields[name] = forms.DateField(
                required=False, label=label, input_formats=["%Y-%m-%d"],
                widget=forms.DateInput(attrs={"type": "date", "class": "input catalogue-input"}),
            )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("from"), cleaned.get("to")
        period = cleaned.get("period", "next30")
        if start or end:
            start = start or self.today
            end = end or start + timedelta(days=29)
        elif period == "custom":
            raise forms.ValidationError("Choose a start or end date for your custom dates.")
        elif period == "today":
            start = end = self.today
        elif period == "tomorrow":
            start = end = self.today + timedelta(days=1)
        elif period == "weekend":
            # On Sunday, retain this weekend rather than jumping to the next one.
            start = self.today + timedelta(days=5 - self.today.weekday())
            end = start + timedelta(days=1)
        else:
            start = self.today
            end = start + timedelta(days=6 if period == "next7" else 29)
        if end < start:
            raise forms.ValidationError("The end date must be on or after the start date.")
        cleaned["range_start"], cleaned["range_end"] = start, end
        return cleaned
