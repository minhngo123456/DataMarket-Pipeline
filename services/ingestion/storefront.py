"""Bounded, same-origin product discovery for public storefronts."""

import json
import re
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from marketdata.contracts import InvalidRecord

MAX_PRODUCTS = 10
MAX_COLLECTION_SECONDS = 20
PRODUCT_TIMEOUT_SECONDS = 3


def same_origin_url(base, href):
    try:
        origin = urlsplit(base)
        parts = urlsplit(urljoin(base, href))
        decoded = unquote(parts.path)
        if (
            parts.scheme not in ("http", "https")
            or parts.scheme != origin.scheme or parts.netloc.lower() != origin.netloc.lower()
            or parts.username or parts.password or "\\" in decoded
            or any(segment in (".", "..") for segment in decoded.split("/"))
        ):
            return None
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
    except ValueError:
        return None


def is_product_url(url):
    return re.fullmatch(
        r"/(?:collections/[^/]+/)?(?:products?|san-pham)/[^/]+/?", urlsplit(url).path,
    ) is not None


def _semantic_product_url(url):
    path = urlsplit(url).path
    return is_product_url(url) or (
        re.fullmatch(r"/[a-zA-Z0-9_-]+(?:\.html)?", path) is not None
        and path.strip("/") not in {"account", "login", "logout", "register", "cart", "search"}
    )


class StorefrontLinks(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base = base
        self.urls = []
        self.preferred = []
        self.collections = []
        self.heading = False
        self.semantic_heading = False
        self.scripts = []
        self.script = None

    def add(self, target, href, product=False):
        url = same_origin_url(self.base, href)
        if url and (not product or is_product_url(url)) and url not in target:
            target.append(url)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("h2", "h3"):
            classes = set((attrs.get("class") or "").split())
            self.heading = bool(classes & {
                "pro-name", "product-title", "woocommerce-loop-product__title",
            })
            self.semantic_heading = bool(classes & {"product-name", "product-name-no-ellipsis"})
        if tag == "a" and attrs.get("href"):
            self.add(self.urls, attrs["href"], product=True)
            if self.heading:
                self.add(self.preferred, attrs["href"], product=True)
            if self.semantic_heading or set((attrs.get("class") or "").split()) & {
                "item-product-name", "product-item-link",
            }:
                url = same_origin_url(self.base, attrs["href"])
                if url and _semantic_product_url(url):
                    self.add(self.preferred, url)
        # Lazy storefront sections often expose their collection before products load.
        handle = attrs.get("data-handle") or ""
        if handle and re.fullmatch(r"(?:/collections/)?[a-zA-Z0-9_-]+", handle):
            self.add(self.collections, "/collections/" + handle.rsplit("/", 1)[-1])
        if tag == "script" and (attrs.get("type") or "").split(";", 1)[0].strip().lower() == "application/ld+json":
            self.script = []

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)

    def handle_endtag(self, tag):
        if tag in ("h2", "h3"):
            self.heading = False
            self.semantic_heading = False
        if tag == "script" and self.script is not None:
            self.scripts.append("".join(self.script))
            self.script = None

    def products(self):
        def walk(node):
            if isinstance(node, list):
                for item in node:
                    walk(item)
            elif isinstance(node, dict):
                kind = node.get("@type", "")
                if kind in ("Product", "ListItem") and isinstance(node.get("url"), str):
                    self.add(self.urls, node["url"])
                if kind == "ListItem" and isinstance(node.get("item"), str):
                    self.add(self.urls, node["item"])
                for key in ("@graph", "itemListElement", "item", "mainEntity"):
                    if key in node:
                        walk(node[key])
        for script in self.scripts:
            try:
                walk(json.loads(script))
            except (ValueError, RecursionError):
                continue
        return (self.preferred or self.urls)[:MAX_PRODUCTS]


def discover(data, url):
    parser = StorefrontLinks(url)
    parser.feed(data.decode("utf-8", errors="replace"))
    return parser


def storefront_feed(data, url, discovery):
    """Only use known public products.json endpoints after detecting the platform."""
    html = data.decode("utf-8", errors="replace")
    haravan = re.search(r"\bHaravan\.shop\s*=", html)
    shopify = re.search(r"\bShopify\.shop\s*=", html)
    if not (haravan or shopify):
        return None
    analytics = "HaravanAnalytics" if haravan else "ShopifyAnalytics"
    currency_pattern = rf"{analytics}\.meta\.currency\s*=\s*['\"]([A-Z]{{3}})['\"]"
    currency = re.search(currency_pattern, html)
    currency = currency[1] if currency else None
    if not currency:
        assignment = re.search(rf"{analytics}\.meta\s*=\s*(\{{)", html)
        if assignment:
            try:
                metadata, _ = json.JSONDecoder().raw_decode(html[assignment.start(1):])
                currency = metadata.get("currency")
            except (ValueError, RecursionError):
                pass
    # products.json uses the shop currency. Shopify.currency.active may instead
    # describe a visitor's presentment currency and must not relabel feed prices.
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise InvalidRecord("html_storefront_currency_missing")
    path = urlsplit(url).path.rstrip("/")
    if is_product_url(url):
        slug = path.rsplit("/", 1)[-1]
        return same_origin_url(url, "/products/" + slug + ".json"), currency
    if re.fullmatch(r"/collections/[a-zA-Z0-9_-]+", path):
        base = same_origin_url(url, path)
    elif discovery.collections:
        base = discovery.collections[0]
    elif shopify:
        base = same_origin_url(url, "/")
    else:
        return None
    return base + "/products.json?page=1&limit=10", currency


def parse_storefront_feed(data, currency, normalize_price):
    try:
        document = json.loads(data)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidRecord("invalid_html_storefront_feed") from exc
    products = document.get("products") if isinstance(document, dict) else None
    if isinstance(document, dict) and isinstance(document.get("product"), dict):
        products = [document["product"]]
    if not isinstance(products, list) or not products:
        raise InvalidRecord("invalid_html_storefront_feed")
    rows = {}
    for product in products[:MAX_PRODUCTS]:
        variants = product.get("variants") if isinstance(product, dict) else None
        if not isinstance(variants, list) or not variants:
            raise InvalidRecord("invalid_html_storefront_feed")
        for variant in variants:
            if not isinstance(variant, dict):
                raise InvalidRecord("invalid_html_storefront_feed")
            if variant.get("available") is False:
                continue
            sku, price = variant.get("sku"), variant.get("price")
            if not isinstance(sku, str) or not sku.strip():
                raise InvalidRecord("html_missing_product_sku")
            if isinstance(price, bool) or not isinstance(price, (str, int, float)):
                raise InvalidRecord("invalid_html_price")
            price = format(Decimal(normalize_price(price)).normalize(), "f")
            row = {"source_sku": sku.strip(), "price": price, "currency": currency}
            if sku.strip() in rows and rows[sku.strip()] != row:
                raise InvalidRecord("ambiguous_html_product")
            rows[sku.strip()] = row
            if len(rows) > 1000:
                raise InvalidRecord("html_exceeds_1000_rows")
    if not rows:
        raise InvalidRecord("invalid_html_storefront_feed")
    return list(rows.values())
