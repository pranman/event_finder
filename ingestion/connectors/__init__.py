"""Small registry of generic, administrator-configured source connectors."""

from .jsonld import fetch as fetch_jsonld
from .ical import fetch as fetch_ical

CONNECTORS = {"jsonld": fetch_jsonld, "ical": fetch_ical}
