"""Bounded discovery of the product cards on Phuc Hy's public homepage."""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from marketdata.contracts import InvalidRecord

MAX_PRODUCTS = 10
MAX_COLLECTION_SECONDS = 20
PRODUCT_TIMEOUT_SECONDS = 3


def is_homepage(url):
    parts = urlsplit(url)
    return (
        parts.scheme == "https" and parts.netloc == "phuchy.vn"
        and parts.path in ("", "/") and not parts.query
    )


class _HomepageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_product_heading = False
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "h3":
            self.in_product_heading = "pro-name" in (attrs.get("class") or "").split()
        if tag != "a" or not self.in_product_heading or len(self.urls) >= MAX_PRODUCTS:
            return
        href = attrs.get("href") or ""
        parts = urlsplit(urljoin("https://phuchy.vn/", href))
        if (
            parts.scheme != "https" or parts.netloc != "phuchy.vn"
            or not re.fullmatch(r"/products/[a-zA-Z0-9_-]+/?", parts.path)
        ):
            return
        # Tracking/query strings do not create another product or select a variant.
        url = urlunsplit(("https", "phuchy.vn", parts.path.rstrip("/"), "", ""))
        if url not in self.urls:
            self.urls.append(url)

    def handle_endtag(self, tag):
        if tag == "h3":
            self.in_product_heading = False


def product_urls(data):
    parser = _HomepageLinks()
    try:
        parser.feed(data.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise InvalidRecord("invalid_html_syntax") from exc
    if not parser.urls:
        raise InvalidRecord("empty_phuc_hy_homepage")
    return parser.urls
