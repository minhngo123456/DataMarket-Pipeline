"""Read embedded Schema.org product prices without executing JavaScript."""

import json
from decimal import Decimal
from html.parser import HTMLParser

from marketdata.contracts import InvalidRecord
from marketdata.product_metadata import product_metadata

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def _schema_type(value, expected):
    values = value if isinstance(value, list) else [value]
    return any(isinstance(item, str) and item.rstrip("/").rsplit("/", 1)[-1] == expected for item in values)


def _product_rows(document):
    """Only traverse explicit graphs/lists; never turn reviews into products."""
    if isinstance(document, list):
        for item in document:
            yield from _product_rows(item)
    elif isinstance(document, dict):
        if _schema_type(document.get("@type"), "Product"):
            offers = document.get("offers", [])
            offers = offers if isinstance(offers, list) else [offers]
            for offer in offers:
                # AggregateOffer lowPrice is a range, not an exact variant price.
                if not isinstance(offer, dict) or not _schema_type(offer.get("@type"), "Offer"):
                    continue
                yield {
                    "source_sku": offer.get("sku") or document.get("sku") or document.get("mpn"),
                    "price": offer.get("price"),
                    "currency": offer.get("priceCurrency"),
                    "source_name": document.get("name"),
                    "source_url": offer.get("url") or document.get("url"),
                }
        for key in ("@graph", "itemListElement", "item", "mainEntity"):
            if key in document:
                yield from _product_rows(document[key])


class ProductHTMLParser(HTMLParser):
    """Fallback for Product JSON-LD and metadata within Product microdata scopes."""

    def __init__(self):
        super().__init__()
        self.products = []
        self.stack = []
        self.scripts = []
        self.script = None
        self.metadata = {}
        self.title = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        product = self.stack[-1][1] if self.stack else None
        if "itemscope" in attrs:
            if _schema_type(attrs.get("itemtype"), "Product"):
                product = {}
                self.products.append(product)
            elif not _schema_type(attrs.get("itemtype"), "Offer"):
                # Brand, review, seller and other nested entities own their fields.
                product = None
        if tag == "script" and (attrs.get("type") or "").split(";", 1)[0].strip().lower() == "application/ld+json":
            self.script = []
        if tag == "meta" and attrs.get("property") and attrs.get("content"):
            self.metadata.setdefault(attrs["property"], set()).add(attrs["content"].strip())
        fields = {"sku": "source_sku", "price": "price", "priceCurrency": "currency"}
        props = [fields[prop] for prop in (attrs.get("itemprop") or "").split() if prop in fields]
        value = attrs.get("content") or (attrs.get("href") if tag == "link" else None)
        if product is not None and value:
            for prop in props:
                product.setdefault(prop, set()).add(value.strip())
        if tag not in VOID_TAGS:
            self.stack.append((tag, product, props if not value else [], []))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if any(entry[0] == "title" for entry in self.stack):
            self.title.append(data)
        if self.script is not None:
            self.script.append(data)
        else:
            for _, product, props, buffer in self.stack:
                if product is not None and props:
                    buffer.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            self.scripts.append("".join(self.script))
            self.script = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                for _, product, props, buffer in self.stack[index:]:
                    value = "".join(buffer).strip()
                    if product is not None and value:
                        for prop in props:
                            product.setdefault(prop, set()).add(value)
                del self.stack[index:]
                break

    def rows(self, normalize_price):
        def canonical(field, value):
            if field == "price":
                return format(Decimal(normalize_price(value)).normalize(), "f")
            return value.upper() if field == "currency" else value

        candidates = []
        for product in self.products:
            if all(field in product for field in ("source_sku", "price", "currency")):
                fields = {field: {canonical(field, value) for value in values}
                          for field, values in product.items()}
                if any(len(values) != 1 for values in fields.values()):
                    raise InvalidRecord("ambiguous_html_product")
                candidates.append({field: next(iter(values)) for field, values in fields.items()})
        for script in self.scripts:
            try:
                document = json.loads(script)
            except (ValueError, RecursionError):
                continue
            candidates.extend(_product_rows(document))
        if not candidates and self.metadata.get("og:type") in ({"product"}, {"og:product"}):
            fields = {"product:retailer_item_id": "source_sku", "product:price:amount": "price",
                      "product:price:currency": "currency"}
            if all(key in self.metadata for key in fields):
                if any(len(self.metadata[key]) != 1 for key in fields):
                    raise InvalidRecord("ambiguous_html_product")
                candidates.append({field: next(iter(self.metadata[key])) for key, field in fields.items()})
        # A page may publish both formats. Conflicting prices for one SKU are
        # rejected rather than silently choosing a variant or an outdated value.
        unique = {}
        for row in candidates:
            if not all(isinstance(row.get(field), (str, int, float)) and not isinstance(row[field], bool)
                       and str(row[field]).strip() for field in ("source_sku", "price", "currency")):
                continue
            metadata = product_metadata(row)
            row = {key: canonical(key, str(row[key]).strip()) for key in ("source_sku", "price", "currency")}
            key = row["source_sku"]
            if key in unique and any(unique[key][field] != row[field] for field in row):
                raise InvalidRecord("ambiguous_html_product")
            unique[key] = {**unique.get(key, {}), **row, **metadata}
        if len(unique) == 1:
            row = next(iter(unique.values()))
            title = next(iter(self.metadata.get("og:title", [])), "") or "".join(self.title)
            if "source_name" not in row and title:
                row.update(product_metadata({"source_name": title}))
        return list(unique.values())
