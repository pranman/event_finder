from datetime import date, datetime, timezone as datetime_timezone
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from events.models import Category, City, Event, ImportRun, Source, SourceListing


@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=["testserver"])
class CatalogueViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.london = City.objects.create(
            name="London", slug="london", country_code="GB", timezone="Europe/London", currency="GBP",
        )
        cls.toronto = City.objects.create(
            name="Toronto", slug="toronto", country_code="CA", timezone="America/Toronto", currency="CAD",
        )
        cls.music = Category.objects.create(name="Music", slug="music")
        cls.source = Source.objects.create(
            name="Local calendar", slug="local-calendar", city=cls.london,
            connector="ical", url="https://example.com/events.ics",
        )

    def setUp(self):
        self.clock = patch("django.utils.timezone.now", return_value=datetime(
            2026, 10, 6, 10, tzinfo=datetime_timezone.utc,
        ))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.url = reverse("events:city_list", kwargs={"city_slug": self.london.slug})

    def make_event(self, **kwargs):
        return Event.objects.create(**{
            "city": self.london, "title": "A good evening", "start_date": date(2026, 10, 6), **kwargs,
        })

    def detail_url(self, event, city=None):
        return reverse("events:event_detail", kwargs={
            "city_slug": (city or event.city).slug, "public_id": event.public_id,
        })

    def result_ids(self, response):
        return {event.pk for event in response.context["page_obj"]}

    def test_home_offers_multiple_cities_without_assuming_a_default(self):
        response = self.client.get(reverse("events:home"))
        self.assertContains(response, "London")
        self.assertContains(response, "Toronto")
        self.assertContains(response, reverse("events:city_list", kwargs={"city_slug": "toronto"}))

    def test_single_active_city_redirects_and_no_active_cities_has_empty_state(self):
        self.toronto.is_active = False
        self.toronto.save()
        self.assertRedirects(self.client.get(reverse("events:home")), self.url)
        self.london.is_active = False
        self.london.save()
        response = self.client.get(reverse("events:home"))
        self.assertContains(response, "We’re getting the first city ready.")

    def test_browse_only_returns_published_scheduled_events_in_selected_city(self):
        expected = self.make_event()
        self.make_event(city=self.toronto)
        self.make_event(is_published=False)
        self.make_event(status=Event.Status.CANCELLED)
        self.make_event(status=Event.Status.POSTPONED)
        self.assertEqual(self.result_ids(self.client.get(self.url)), {expected.pk})

    def test_inactive_or_unknown_city_is_not_public(self):
        event = self.make_event()
        self.london.is_active = False
        self.london.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(self.detail_url(event)).status_code, 404)
        self.assertEqual(self.client.get("/unknown/events/").status_code, 404)

    def test_default_window_is_thirty_inclusive_dates(self):
        first = self.make_event()
        last = self.make_event(start_date=date(2026, 11, 4))
        self.make_event(start_date=date(2026, 11, 5))
        self.make_event(start_date=date(2026, 10, 5))
        response = self.client.get(self.url)
        self.assertEqual(self.result_ids(response), {first.pk, last.pk})
        self.assertEqual(response.context["range_end"], date(2026, 11, 4))

    def test_today_includes_multiday_events_overlapping_selected_date(self):
        ongoing = self.make_event(start_date=date(2026, 10, 1), end_date=date(2026, 10, 7))
        ending_today = self.make_event(start_date=date(2026, 10, 1), end_date=date(2026, 10, 6))
        self.make_event(start_date=date(2026, 10, 1), end_date=date(2026, 10, 5))
        self.make_event(start_date=date(2026, 10, 7))
        response = self.client.get(self.url, {"period": "today"})
        self.assertEqual(self.result_ids(response), {ongoing.pk, ending_today.pk})

    def test_tomorrow_and_next_seven_days_are_inclusive(self):
        today = self.make_event()
        tomorrow = self.make_event(start_date=date(2026, 10, 7))
        seventh = self.make_event(start_date=date(2026, 10, 12))
        self.make_event(start_date=date(2026, 10, 13))
        self.assertEqual(self.result_ids(self.client.get(self.url, {"period": "tomorrow"})), {tomorrow.pk})
        self.assertEqual(self.result_ids(self.client.get(self.url, {"period": "next7"})), {today.pk, tomorrow.pk, seventh.pk})

    def test_weekend_on_sunday_keeps_the_current_weekend(self):
        saturday = self.make_event(start_date=date(2026, 10, 10))
        sunday = self.make_event(start_date=date(2026, 10, 11))
        self.make_event(start_date=date(2026, 10, 17))
        with patch("django.utils.timezone.now", return_value=datetime(2026, 10, 11, 12, tzinfo=datetime_timezone.utc)):
            response = self.client.get(self.url, {"period": "weekend"})
        self.assertEqual(self.result_ids(response), {saturday.pk, sunday.pk})

    def test_today_uses_each_city_timezone_after_daylight_saving_change(self):
        london_monday = self.make_event(start_date=date(2026, 3, 30))
        self.make_event(start_date=date(2026, 3, 29))
        toronto_sunday = self.make_event(city=self.toronto, start_date=date(2026, 3, 29))
        self.make_event(city=self.toronto, start_date=date(2026, 3, 30))
        with patch("django.utils.timezone.now", return_value=datetime(2026, 3, 29, 23, 30, tzinfo=datetime_timezone.utc)):
            london = self.client.get(self.url, {"period": "today"})
            toronto = self.client.get(reverse("events:city_list", kwargs={"city_slug": "toronto"}), {"period": "today"})
        self.assertEqual(self.result_ids(london), {london_monday.pk})
        self.assertEqual(self.result_ids(toronto), {toronto_sunday.pk})
        self.assertEqual(london.context["today"], date(2026, 3, 30))
        self.assertEqual(toronto.context["today"], date(2026, 3, 29))

    def test_custom_dates_override_preset_and_match_an_overlap(self):
        event = self.make_event(start_date=date(2026, 9, 1), end_date=date(2026, 9, 20))
        self.make_event()
        response = self.client.get(self.url, {"period": "today", "from": "2026-09-10", "to": "2026-09-12"})
        self.assertEqual(self.result_ids(response), {event.pk})
        self.assertContains(response, 'value="2026-09-10"')

    def test_search_category_and_price_filters_combine(self):
        target = self.make_event(title="Evening recital", venue_name="Garden Hall", price_status="free")
        target.categories.add(self.music)
        paid = self.make_event(title="Evening recital", venue_name="Garden Hall", price_status="paid")
        paid.categories.add(self.music)
        self.make_event(venue_name="Garden Hall", price_status="free")
        response = self.client.get(self.url, {"q": "garden", "category": "music", "price": "free"})
        self.assertEqual(self.result_ids(response), {target.pk})

    def test_unknown_price_is_distinct_from_free(self):
        unknown = self.make_event(price_status="unknown")
        self.make_event(price_status="free")
        response = self.client.get(self.url, {"price": "unknown"})
        self.assertEqual(self.result_ids(response), {unknown.pk})
        self.assertContains(response, "Price not listed")

    def test_invalid_filters_render_validation_instead_of_unfiltered_results(self):
        self.make_event(title="Do not accidentally expose broad results")
        for params in (
            {"from": "not-a-date"}, {"from": "2026-10-12", "to": "2026-10-01"},
            {"period": "invalid"}, {"period": "custom"}, {"category": "nonexistent"},
            {"price": "expensive"}, {"q": "x" * 201},
        ):
            with self.subTest(params=params):
                response = self.client.get(self.url, params)
                self.assertContains(response, "Please check your filters.")
                self.assertFalse(self.result_ids(response))
                self.assertNotContains(response, "Do not accidentally expose broad results")

    def test_detail_rejects_cross_city_and_unpublished_events(self):
        event = self.make_event()
        self.assertEqual(self.client.get(self.detail_url(event, self.toronto)).status_code, 404)
        event.is_published = False
        event.save()
        self.assertEqual(self.client.get(self.detail_url(event)).status_code, 404)

    def test_cancelled_detail_remains_visible_with_status_and_honest_unknowns(self):
        event = self.make_event(status="cancelled")
        response = self.client.get(self.detail_url(event))
        self.assertContains(response, "This event is cancelled.")
        self.assertContains(response, "Time not listed")
        self.assertContains(response, "Price not listed")
        self.assertContains(response, "Venue not listed")

    def test_external_text_is_escaped_and_non_web_urls_are_not_linked(self):
        event = self.make_event(
            title='<script>alert("title")</script>', description='<img src=x onerror="alert(1)">',
            url="javascript:alert(1)",
        )
        self.source.name = '<script>alert("source")</script>'
        self.source.save()
        SourceListing.objects.create(
            source=self.source, event=event, external_id="1", source_url="javascript:alert(2)",
        )
        response = self.client.get(self.detail_url(event))
        self.assertContains(response, "&lt;script&gt;")
        self.assertContains(response, "&lt;img")
        self.assertNotContains(response, "<script>")
        self.assertNotContains(response, 'href="javascript:')
        self.assertNotContains(response, "<img src=x")

    def test_provenance_and_refresh_failure_are_visible_without_internal_errors(self):
        event = self.make_event(url="https://example.com/tickets")
        SourceListing.objects.create(
            source=self.source, event=event, external_id="1", source_url="https://example.com/original",
        )
        self.source.last_success_at = datetime(2026, 10, 5, 12, tzinfo=datetime_timezone.utc)
        self.source.save()
        ImportRun.objects.create(source=self.source, status="failed", error="Internal network detail secret")
        response = self.client.get(self.detail_url(event))
        self.assertContains(response, 'href="https://example.com/original"')
        self.assertContains(response, "Last seen")
        self.assertContains(response, "Refresh delayed")
        self.assertContains(response, "5 Oct 2026, 13:00")
        self.assertNotContains(response, "Internal network detail secret")

    def test_pagination_is_stable_and_preserves_filters(self):
        for index in range(15):
            self.make_event(title=f"A & B evening {index:02}")
        filters = {"q": "A & B", "period": "today", "price": "unknown"}
        first = self.client.get(self.url, filters)
        second = self.client.get(self.url, {**filters, "page": 2})
        self.assertEqual(len(self.result_ids(first)), 12)
        self.assertEqual(len(self.result_ids(second)), 3)
        self.assertFalse(self.result_ids(first) & self.result_ids(second))
        self.assertContains(first, "q=A+%26+B&amp;period=today&amp;price=unknown&amp;page=2")
        self.assertEqual(first.context["page_obj"][0].title, "A & B evening 00")
        self.assertEqual(second.context["page_obj"][0].title, "A & B evening 12")
        self.assertEqual(self.client.get(self.url, {"page": "bad"}).context["page_obj"].number, 1)

    def test_empty_search_has_clear_recovery_and_labelled_form(self):
        response = self.client.get(self.url, {"q": "no matching event"})
        self.assertContains(response, "No events match these filters yet.")
        self.assertContains(response, 'for="id_q"')
        self.assertContains(response, 'href="#main-content"')
        self.assertContains(response, "Reset filters")

    def test_filters_remain_available_without_javascript_and_validation_is_visible(self):
        response = self.client.get(self.url, {"from": "invalid"})
        self.assertContains(response, '<details class="filter-disclosure" data-mobile-collapse open>')
        self.assertContains(response, '<div class="filter-errors" role="alert">')
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'id="id_from_error"')
