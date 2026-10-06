"""Regressions for infrastructure paths shadowed by generic city URLs (#10)."""

from contextlib import contextmanager
import importlib

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings
from django.urls import NoReverseMatch, clear_url_caches, resolve, reverse

from events.models import City


@contextmanager
def project_routes(*, debug):
    """URL patterns are built at import time, so test each environment explicitly."""
    from Project import urls
    try:
        with override_settings(DEBUG=debug, ROOT_URLCONF="Project.urls"):
            importlib.reload(urls)
            clear_url_caches()
            yield
    finally:
        importlib.reload(urls)
        clear_url_caches()


class ReservedRouteTests(SimpleTestCase):
    def test_development_reload_endpoint_precedes_generic_city_route(self):
        with project_routes(debug=True):
            match = resolve("/__reload__/events/")
            self.assertEqual(match.view_name, "django_browser_reload:events")
            self.assertEqual(reverse("django_browser_reload:events"), "/__reload__/events/")

    def test_generic_cities_still_resolve_and_admin_keeps_its_namespace(self):
        with project_routes(debug=True):
            for slug in ("london", "new-york", "berlin"):
                with self.subTest(slug=slug):
                    url = reverse("events:city_list", kwargs={"city_slug": slug})
                    self.assertEqual(url, f"/{slug}/events/")
                    match = resolve(url)
                    self.assertEqual(match.view_name, "events:city_list")
                    self.assertEqual(match.kwargs["city_slug"], slug)
            self.assertEqual(resolve("/admin/").namespace, "admin")

    def test_development_reload_is_not_registered_in_production(self):
        with project_routes(debug=False):
            with self.assertRaises(NoReverseMatch):
                reverse("django_browser_reload:events")
            self.assertEqual(resolve("/berlin/events/").view_name, "events:city_list")

    def test_city_validation_rejects_reserved_infrastructure_slugs(self):
        for slug in ("admin", "__reload__", "static", "media"):
            with self.subTest(slug=slug):
                city = City(name="Reserved", slug=slug, country_code="GB", timezone="Europe/London")
                with self.assertRaises(ValidationError) as error:
                    city.full_clean(validate_unique=False, validate_constraints=False)
                self.assertIn("slug", error.exception.message_dict)

    def test_city_validation_accepts_normal_generic_slugs(self):
        for slug in ("london", "new-york", "berlin", "admin-city"):
            with self.subTest(slug=slug):
                City(name="City", slug=slug, country_code="GB", timezone="UTC").full_clean(
                    validate_unique=False, validate_constraints=False,
                )
