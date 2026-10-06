"""Bounded HTTP and parsing helpers shared by configured event connectors."""

from html import unescape
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

USER_AGENT = "CityEvents/1.0 (public event catalogue; scheduled source refresh)"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class SourceFormatError(ValueError):
    """The source answered, but did not provide the expected event format."""


def http_url(value, base=""):
    """Return an absolute HTTP(S) URL or an empty string for unsafe links."""
    if not isinstance(value, str) or not value.strip():
        return ""
    url = urldefrag(urljoin(base, value.strip())).url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    return url


def same_host_url(value, base):
    candidate = http_url(value, base)
    return candidate if candidate and urlparse(candidate).netloc == urlparse(base).netloc else ""


def plain_text(value):
    if not isinstance(value, str):
        return ""
    soup = BeautifulSoup(unescape(value), "html.parser")
    for element in soup(["script", "style"]):
        element.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def limit(config, key, default, maximum):
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise SourceFormatError(f"{key} must be an integer between 1 and {maximum}")
    return value


def client():
    return httpx.Client(timeout=20.0, headers={"User-Agent": USER_AGENT}, follow_redirects=False)


def get(client, url):
    current = http_url(url)
    if not current:
        raise SourceFormatError("Source URL must use HTTP or HTTPS")
    for _ in range(6):
        response = client.get(current)
        if response.is_redirect:
            current = same_host_url(response.headers.get("location", ""), current)
            if not current:
                raise SourceFormatError("Source redirected to a different host or an unsafe URL")
            continue
        response.raise_for_status()
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise SourceFormatError("Source response exceeds the 5 MiB limit")
        return response
    raise SourceFormatError("Source exceeded the redirect limit")


def index_pages(client, source):
    """Yield a bounded set of index pages, following only configured links."""
    config = source.config
    max_pages = limit(config, "max_pages", 1, 10)
    next_selector = config.get("next_page_selector")
    url = http_url(source.url)
    seen = set()
    while url and url not in seen and len(seen) < max_pages:
        seen.add(url)
        response = get(client, url)
        soup = BeautifulSoup(response.text, "html.parser")
        yield str(response.url), soup
        next_link = soup.select_one(next_selector) if next_selector else None
        url = same_host_url(next_link.get("href", ""), url) if next_link else ""


def detail_links(soup, page_url, selector):
    seen = set()
    for anchor in soup.select(selector):
        url = same_host_url(anchor.get("href", ""), page_url)
        if url and url not in seen:
            seen.add(url)
            yield url


def categories(config):
    values = config.get("categories", [])
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise SourceFormatError("categories must be a list of category names")
    return tuple(plain_text(value) for value in values if value.strip())
