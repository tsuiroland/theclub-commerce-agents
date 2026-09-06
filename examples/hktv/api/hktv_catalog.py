# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""HKTV Mall's catalog over the Algolia index its own storefront searches — the
discovery phase of this example's roadmap. www.hktvmall.com renders keyword search
client-side against Algolia (index ``hktvProduct``), shipping the app id and a
search-only API key to every browser (algoliaSearchAdapter.js); this backend queries
that index anonymously, so search and product details are read-only JSON with no
scraping. Each HKTV Mall record is one sku per colour or size — siblings share a
``baseProduct`` but are separate records of their own — so nothing here models a
family: every hit maps to a plain Product. HKTV Mall's cart, order history, policies,
and fulfillment are not wired yet, so those methods raise and their tools answer that
they are temporarily unavailable (the config keeps their switches on, per the backend
contract).

Prices are HKD exactly as the index serves them (``sellingPrice``, its ``priceList``
BUY entry as the fallback), and the search's min/max price filters map onto Algolia
numeric filters over that field. Other filters and sorts are ignored here."""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from shopping_agent import (
    Cart,
    Order,
    Product,
    ProductDetails,
    SearchFilters,
    ShoppingSessionContext,
    StorefrontBackend,
    UserPreferences,
)

_logger = logging.getLogger("hktv.catalog")

# Published by the site to every browser: a search-only key over the public catalog,
# the same trust class as The Club's anonymous AEM page models. A deployment replaces
# them through the constructor, not by editing here.
DEFAULT_APP_ID = "8RN1Y79F02"
DEFAULT_SEARCH_KEY = "a4a336abc62ab842842a81de642b484a"
DEFAULT_INDEX = "hktvProduct"
SHOP_BASE = "https://www.hktvmall.com"
PRODUCT_URL_PREFIX = f"{SHOP_BASE}/hktv/en/"

# The direct record read: the index filters on its own ``code`` field. (The site's
# adapter matches user-typed codes against ``allProductSearchCode``, an array of
# alternate codes a record answers to — probed: ``code`` and ``objectID`` filter,
# ``allProductSearchCode`` does not hold the record id.)
_CODE_FILTER = 'code:"{code}"'


def _not_wired(system: str, phase: str) -> RuntimeError:
    # The executor relays any exception but NotOffered as the tool being temporarily
    # unavailable and logs this message: the honest state for a system that exists
    # and is not wired yet.
    return RuntimeError(f"HKTV Mall's {system} is not wired yet ({phase})")


def _buy_price(hit: dict[str, Any]) -> float:
    """The HKD price a hit sells at: its own selling price, the BUY list as fallback."""
    selling = hit.get("sellingPrice")
    if isinstance(selling, (int, float)) and selling > 0:
        return float(selling)
    for entry in hit.get("priceList") or []:
        if entry.get("priceType") == "BUY" and isinstance(entry.get("value"), (int, float)):
            return float(entry["value"])
    return 0.0


def _image(hit: dict[str, Any]) -> str | None:
    for image in hit.get("images") or []:
        if image.get("imageType") == "PRIMARY":
            return image.get("url")
    return None


def _first(hit: dict[str, Any], key: str) -> str | None:
    """The index serves repeated fields (names, categories) as arrays."""
    values = hit.get(key) or []
    return values[0] if values else None


def _rating(hit: dict[str, Any]) -> float | None:
    rating = hit.get("averageRating")
    if isinstance(rating, (int, float)) and rating > 0:
        return round(min(float(rating), 5.0), 1)
    return None


class HktvMallCatalog(StorefrontBackend):
    """Reads the Algolia index www.hktvmall.com's own search runs on, one async client
    for the process, anonymously (the browser-published search key). Every method acts
    for the session as a guest: HKTV Mall's account surface is a later phase."""

    store_name = "HKTV Mall"

    def __init__(
        self,
        *,
        app_id: str = DEFAULT_APP_ID,
        api_key: str = DEFAULT_SEARCH_KEY,
        index_name: str = DEFAULT_INDEX,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._index = index_name
        self._query_url = f"https://{app_id.lower()}-dsn.algolia.net/1/indexes/*/queries"
        self._headers = {
            "X-Algolia-Application-Id": app_id,
            "X-Algolia-API-Key": api_key,
        }
        self._client = client or httpx.AsyncClient(timeout=15.0)

    async def _hits(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        body = {"requests": [{"indexName": self._index, "params": urlencode(params)}]}
        started = time.monotonic()
        response = await self._client.post(self._query_url, json=body, headers=self._headers)
        _logger.info(
            "hktv <- Algolia %s %s (%.0f ms)",
            self._index,
            params,
            (time.monotonic() - started) * 1000,
        )
        response.raise_for_status()
        results = response.json().get("results") or []
        if not results:
            raise RuntimeError("Algolia returned no result set")
        result = results[0]
        if message := result.get("message"):
            raise RuntimeError(f"Algolia rejected the query: {message}")
        return result.get("hits") or []

    # -- Mapping ------------------------------------------------------------------

    def _summary(self, hit: dict[str, Any]) -> Product:
        attributes: dict[str, str] = {
            "store": self.store_name,
            "merchant": str(hit.get("storeNameEn") or ""),
        }
        if search_code := hit.get("productSearchCode"):
            attributes["search_code"] = str(search_code)
        if url_path := hit.get("urlEn"):
            attributes["url"] = PRODUCT_URL_PREFIX + url_path
        if points := hit.get("loyaltyPoint"):
            attributes["loyalty_point"] = str(points)
        if delivery := hit.get("deliveryTime"):
            attributes["delivery"] = str(delivery)
        return Product(
            product_id=str(hit["code"]),
            title=str(hit.get("nameEn") or hit.get("code")),
            brand=hit.get("brandEn") or hit.get("brand"),
            price=_buy_price(hit),
            currency="HKD",
            rating=_rating(hit),
            review_count=hit.get("numberOfReviews"),
            image_url=_image(hit),
            category=_first(hit, "mainCatNameEn"),
            labels=[hit["promotionTextEn"]] if hit.get("promotionTextEn") else [],
            in_stock=bool(hit.get("hasStock")),
            short_description=hit.get("summaryEn") or None,
            attributes=attributes,
        )

    def _specs(self, hit: dict[str, Any]) -> dict[str, str]:
        specs: dict[str, str] = {}
        if brand := hit.get("brandEn") or hit.get("brand"):
            specs["brand"] = str(brand)
        if colours := hit.get("numberOfColors"):
            specs["colours"] = str(colours)  # siblings are separate skus, not options
        if origin := hit.get("countryOfOriginEn"):
            specs["country_of_origin"] = str(origin)
        if packing := hit.get("packingSpecEn"):
            specs["packing"] = str(packing)
        return specs

    # -- Catalog ------------------------------------------------------------------

    async def search_products(
        self,
        session: ShoppingSessionContext,
        query: str,
        filters: SearchFilters | None = None,
        limit: int = 8,
    ) -> list[Product]:
        params: dict[str, Any] = {"query": query, "hitsPerPage": limit}
        numeric: list[str] = []
        if filters is not None:
            if filters.min_price is not None:
                numeric.append(f"sellingPrice>={filters.min_price:g}")
            if filters.max_price is not None:
                numeric.append(f"sellingPrice<={filters.max_price:g}")
            if numeric:
                params["numericFilters"] = ",".join(numeric)
        hits = await self._hits(params)
        return [self._summary(hit) for hit in hits if not hit.get("invisible")]

    async def get_product_details(
        self, session: ShoppingSessionContext, product_id: str
    ) -> ProductDetails | None:
        hits = await self._hits(
            {"query": "", "hitsPerPage": 2, "filters": _CODE_FILTER.format(code=product_id)}
        )
        # The lookup filter is exact; a hit that is not the id asked for is no match.
        hit = next((h for h in hits if str(h.get("code")) == product_id), None)
        if hit is None:
            return None
        summary = self._summary(hit)
        return ProductDetails(**summary.model_dump() | {"specs": self._specs(hit)})

    # -- Guest context ------------------------------------------------------------

    async def get_preferences(self, session: ShoppingSessionContext) -> UserPreferences:
        return UserPreferences(user_id=session.user_id, preferences={"store": "hktv"})

    async def get_cart(self, session: ShoppingSessionContext) -> Cart:
        # A read the turn always makes; nothing is wired to write one, so it is empty.
        return Cart(currency="HKD")

    # -- Not wired yet ------------------------------------------------------------

    async def add_to_cart(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        raise _not_wired("cart", "later")

    async def update_cart_item(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        raise _not_wired("cart", "later")

    async def remove_from_cart(self, session: ShoppingSessionContext, product_id: str) -> Cart:
        raise _not_wired("cart", "later")

    async def get_orders(self, session: ShoppingSessionContext, limit: int = 5) -> list[Order]:
        raise _not_wired("order history", "after the member account")

    async def get_order(self, session: ShoppingSessionContext, order_id: str) -> Order | None:
        raise _not_wired("order history", "after the member account")

    async def search_policies(self, session: ShoppingSessionContext, query: str):
        raise _not_wired("policy library", "later")

    async def get_fulfillment_options(
        self, session: ShoppingSessionContext, product_ids: list[str]
    ):
        raise _not_wired("fulfillment", "later")
