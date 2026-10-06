"""Read-only catalogue views; importing events always happens separately."""

from urllib.parse import urlsplit

from django.core.paginator import Paginator
from django.db.models import OuterRef, Prefetch, Q, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import EventFilterForm
from .models import City, Event, ImportRun, Source, SourceListing


def public_url(value):
    """Do not turn malformed or non-web URLs from external data into links."""
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""
    except ValueError:
        return ""


def active_cities():
    return City.objects.filter(is_active=True)


def home(request):
    cities = list(active_cities())
    if len(cities) == 1:
        return redirect("events:city_list", city_slug=cities[0].slug)
    return render(request, "events/cities.html", {"cities": cities})


def source_statuses(city):
    latest_run = ImportRun.objects.filter(source=OuterRef("pk")).order_by("-started_at", "-pk")
    return Source.objects.filter(city=city, enabled=True).annotate(
        latest_status=Subquery(latest_run.values("status")[:1]),
    )


def city_list(request, city_slug):
    city = get_object_or_404(active_cities(), slug=city_slug)
    today = timezone.localdate(timezone=city.tzinfo)
    form = EventFilterForm(request.GET, today=today)
    events = Event.objects.filter(city=city, is_published=True, status=Event.Status.SCHEDULED)
    if form.is_valid():
        filters = form.cleaned_data
        start, end = filters["range_start"], filters["range_end"]
        events = events.filter(start_date__lte=end).filter(
            Q(end_date__gte=start) | Q(end_date__isnull=True, start_date__gte=start),
        )
        if filters["q"]:
            events = events.filter(
                Q(title__icontains=filters["q"]) | Q(description__icontains=filters["q"])
                | Q(venue_name__icontains=filters["q"]),
            )
        if filters["category"]:
            events = events.filter(categories=filters["category"])
        if filters["price"]:
            events = events.filter(price_status=filters["price"])
    else:
        start = end = None
        events = events.none()
    events = events.prefetch_related("categories").order_by("start_date", "start_time", "title", "pk")
    page = Paginator(events, 12).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "events/event_list.html", {
        "city": city, "cities": active_cities(), "form": form, "page_obj": page,
        "range_start": start, "range_end": end, "today": today,
        "pagination_query": query.urlencode(), "sources": source_statuses(city),
    })


def event_detail(request, city_slug, public_id):
    city = get_object_or_404(active_cities(), slug=city_slug)
    event = get_object_or_404(
        Event.objects.filter(city=city, is_published=True).prefetch_related(
            "categories", Prefetch("source_listings", queryset=SourceListing.objects.select_related("source")),
        ),
        public_id=public_id,
    )
    listings = list(event.source_listings.all())
    for listing in listings:
        listing.safe_url = public_url(listing.source_url or listing.source.url)
    return render(request, "events/event_detail.html", {
        "city": city, "cities": active_cities(), "event": event, "listings": listings,
        "event_url": public_url(event.url), "sources": source_statuses(city),
    })
