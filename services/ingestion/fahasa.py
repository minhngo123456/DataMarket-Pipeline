"""Read Fahasa's public homepage slider and explicit product metadata."""

import json
import re
from urllib.parse import urlencode, urlsplit

from marketdata.contracts import InvalidRecord
from services.ingestion.product_html import ProductHTMLParser, _schema_type
from services.ingestion.storefront import MAX_PRODUCTS, same_origin_url


def is_site(url):
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.netloc in ("www.fahasa.com", "fahasa.com")


def is_homepage(url):
    return is_site(url) and urlsplit(url).path in ("", "/") and not urlsplit(url).query


def is_product_url(url):
    return is_site(url) and re.fullmatch(r"/[a-zA-Z0-9_-]+\.html", urlsplit(url).path) is not None


def homepage_feed(data, url):
    if not is_homepage(url):
        raise InvalidRecord("invalid_fahasa_product_feed")
    html = data.decode("utf-8", errors="replace")
    assignment = re.search(r"\bvar\s+current_block_data_str\s*=\s*", html)
    try:
        if not assignment:
            raise ValueError
        literal, _ = json.JSONDecoder().raw_decode(html[assignment.end():])
        blocks = json.loads(literal)
        first = next(iter(blocks[0].values()))
        ids = first["list"]
        if not isinstance(ids, str) or not re.fullmatch(r"[0-9]+(?:,[0-9]+)*", ids):
            raise ValueError
    except (ValueError, TypeError, KeyError, IndexError, StopIteration, AttributeError, RecursionError) as exc:
        raise InvalidRecord("invalid_fahasa_product_feed") from exc
    # Do not execute JavaScript, follow an arbitrary script URL, or forward any
    # session/customer fields. This endpoint only reads a public product list.
    endpoint = same_origin_url(url, "/tabslider/index/getdata/") + "/"
    return endpoint + "?" + urlencode({"limit": MAX_PRODUCTS, "list": ",".join(ids.split(",")[:MAX_PRODUCTS])})


def product_urls(data, base):
    try:
        products = json.loads(data)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidRecord("invalid_fahasa_product_feed") from exc
    if not isinstance(products, list) or not products:
        raise InvalidRecord("invalid_fahasa_product_feed")
    urls = []
    for product in products[:MAX_PRODUCTS]:
        href = product.get("product_url") if isinstance(product, dict) else None
        url = same_origin_url(base, href) if isinstance(href, str) else None
        if not url or not is_product_url(url):
            raise InvalidRecord("invalid_fahasa_product_feed")
        if url not in urls:
            urls.append(url)
    return urls


def product_rows(data, url, normalize_price):
    """Fahasa exposes its SKU in JS and an exact Offer price in JSON-LD."""
    if not is_product_url(url):
        return []
    html = data.decode("utf-8", errors="replace")
    skus = set(re.findall(r"\bvar\s+sku_product\s*=\s*['\"]([a-zA-Z0-9_-]{1,128})['\"]\s*;", html))
    if not skus:
        return []
    if len(skus) != 1:
        raise InvalidRecord("ambiguous_html_product")
    parser = ProductHTMLParser()
    parser.feed(html)
    candidates = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if _schema_type(node.get("@type"), "Product"):
                offers = node.get("offers", [])
                for offer in offers if isinstance(offers, list) else [offers]:
                    if not isinstance(offer, dict) or not _schema_type(offer.get("@type"), "Offer"):
                        continue
                    # Recommendations or another edition must not supply this book's price.
                    href = offer.get("url") or node.get("url")
                    if not isinstance(href, str) or same_origin_url(url, href) != same_origin_url(url, url):
                        continue
                    candidates.append({"source_sku": next(iter(skus)), "price": offer.get("price"),
                                       "currency": offer.get("priceCurrency"), "source_name": node.get("name")})
            for key in ("@graph", "mainEntity"):
                if key in node:
                    walk(node[key])

    for script in parser.scripts:
        try:
            walk(json.loads(script))
        except (ValueError, RecursionError):
            continue
    # Reuse the normal structured-product validation and conflict detection.
    parser.products = []
    parser.scripts = [json.dumps([{"@type": "Product", "sku": row["source_sku"], "name": row.get("source_name"), "offers": {
        "@type": "Offer", "price": row["price"], "priceCurrency": row["currency"],
    }} for row in candidates])]
    parser.metadata = {}
    return parser.rows(normalize_price)
