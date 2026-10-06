"""Small registry of generic, administrator-configured source connectors."""

from .jsonld import fetch as fetch_jsonld

CONNECTORS = {"jsonld": fetch_jsonld}
