# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The Club's live read-only catalog over shop.theclub.com.hk's Magento GraphQL — Phases
1 and 2a of this example's roadmap. Search and product details are wired, with
Clubpoints pricing overlaid from The Club's AEM shopping pages (aem_price_overlay.py)
so a points-priced product leads with its CP price and a points-budget search answers
from the tiles. The Club's other systems exist but are not wired yet, so those methods
raise and their tools answer that they are temporarily unavailable (the config keeps
their switches on, per the backend contract).

Anonymous GraphQL serves the HKD cash price only: Clubpoints redemption prices are
member-gated in Magento and arrive with the member token (Phase 2b). Until then the
overlay's price stands, ``clubpoints`` from Magento is taken when served, and
pure-redemption items the overlay does not cover (observed convention: ``CR-`` SKU
prefix, HKD final price of 0) carry a price note the assistant can state plainly.
Text search plus the clubpoints_min/clubpoints_max filter dimensions are served;
other filters and sorts are ignored here."""

from __future__ import annotations

import html
import logging
import re
import time
from typing import Any

import httpx

from shopping_agent import (
    Product,
    ProductDetails,
    SearchFilters,
    ShoppingSessionContext,
    StorefrontBackend,
    UserPreferences,
)

from .aem_price_overlay import AemPriceOverlay

_logger = logging.getLogger("theclub.magento")

DEFAULT_GRAPHQL_URL = "https://shop.theclub.com.hk/graphql"
DEFAULT_STORE = "en_US"

SEARCH_QUERY = """
query SearchProducts($q: String!, $limit: Int!) {
  products(search: $q, pageSize: $limit) {
    items {
      sku
      name
      stock_status
      clubpoints
      small_image { url }
      categories { name }
      price_range {
        minimum_price {
          regular_price { value }
          final_price { value currency }
        }
      }
      ... on ConfigurableProduct {
        variants {
          attributes { code label }
          product {
            sku
            stock_status
            price_range { minimum_price { final_price { value currency } } }
          }
        }
      }
    }
  }
}
"""

DETAIL_QUERY = """
query ProductDetail($sku: String!) {
  products(filter: {sku: {eq: $sku}}, pageSize: 2) {
    items {
      sku
      name
      url_key
      stock_status
      clubpoints
      small_image { url }
      categories { name }
      price_range {
        minimum_price {
          regular_price { value }
          final_price { value currency }
        }
      }
      manufacturer
      rating_summary
      review_count
      description { html }
      short_description { html }
      ... on ConfigurableProduct {
        variants {
          attributes { code label }
          product {
            sku
            name
            stock_status
            clubpoints
            small_image { url }
            price_range { minimum_price { final_price { value currency } } }
          }
        }
      }
    }
  }
}
"""

_TAGS = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def _not_wired(system: str, phase: str) -> RuntimeError:
    # The executor relays any exception but NotOffered as the tool being temporarily
    # unavailable and logs this message: the honest state for a system that exists
    # and is not wired yet.
    return RuntimeError(f"The Club's {system} is not wired yet ({phase})")


def _points_bound(value: str | None) -> float | None:
    """A clubpoints_min/clubpoints_max attribute as a number, None when absent."""
    if value is None:
        return None
    try:
        points = float(str(value).replace(",", ""))
    except ValueError:
        return None
    return points if points > 0 else None


def _text(fragment: str | None) -> str | None:
    """Magento serves prose as HTML; the model reads plain fenced text."""
    if not fragment:
        return None
    return _WHITESPACE.sub(" ", html.unescape(_TAGS.sub(" ", fragment))).strip() or None


class ClubMagentoCatalog(StorefrontBackend):
    """Reads shop.theclub.com.hk's anonymous GraphQL storefront, its results enriched
    by the AEM points overlay when one is attached. One async client for the process;
    every method acts for the session but carries no member token yet — ``demo_tier``
    and ``demo_clubpoints`` stand in for the member context, clearly named, until the
    Phase 2b token exists."""

    def __init__(
        self,
        graphql_url: str = DEFAULT_GRAPHQL_URL,
        store_code: str = DEFAULT_STORE,
        client: httpx.AsyncClient | None = None,
        overlay: AemPriceOverlay | None = None,
        demo_tier: str | None = None,
        demo_clubpoints: int | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=15.0)
        self._graphql_url = graphql_url
        self._store_code = store_code
        self._overlay = overlay
        self._demo_tier = demo_tier
        self._demo_clubpoints = demo_clubpoints

    async def _enrich(self, product: Product) -> Product:
        if self._overlay is None:
            return product
        return await self._overlay.enrich(product)

    async def _query(
        self, query: str, variables: dict[str, Any], operation_name: str
    ) -> dict[str, Any]:
        started = time.monotonic()
        response = await self._client.post(
            self._graphql_url,
            json={"query": query, "variables": variables, "operationName": operation_name},
            headers={"Content-Type": "application/json", "Store": self._store_code},
        )
        _logger.info(
            "theclub <- Magento GraphQL %s %s (%.0f ms)",
            operation_name,
            variables,
            (time.monotonic() - started) * 1000,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):  # Magento answers 200 with per-field errors
            raise RuntimeError(f"Magento GraphQL errors: {body['errors']}")
        return body["data"]

    # -- Mapping ------------------------------------------------------------------

    def _attributes(self, item: dict[str, Any]) -> dict[str, str]:
        price, _ = self._price(item)
        attributes: dict[str, str] = {}
        if clubpoints := item.get("clubpoints") or 0:
            attributes["clubpoints"] = str(clubpoints)
        if str(item["sku"]).startswith("CR-"):  # observed: Club Rewards redemptions
            attributes["catalog"] = "rewards"
        if price == 0.0:
            attributes["price_note"] = (
                "Clubpoints redemption item: the points price shows after member login"
            )
        return attributes

    @staticmethod
    def _price(item: dict[str, Any]) -> tuple[float, str]:
        final = item["price_range"]["minimum_price"]["final_price"]
        return float(final["value"]), final.get("currency") or "HKD"

    @staticmethod
    def _options(variants: list[dict[str, Any]]) -> dict[str, list[str]]:
        options: dict[str, list[str]] = {}
        for variant in variants:  # a family: one option value per code, display order
            for attribute in variant.get("attributes") or []:
                values = options.setdefault(attribute["code"], [])
                if attribute["label"] not in values:
                    values.append(attribute["label"])
        return options

    def _summary(self, item: dict[str, Any]) -> Product:
        variants = item.get("variants") or []
        price, currency = self._price(item)
        return Product(
            product_id=item["sku"],
            title=item["name"],
            price=price,
            currency=currency,
            image_url=(item.get("small_image") or {}).get("url"),
            category=next((c["name"] for c in item.get("categories") or []), None),
            in_stock=(
                any(v["product"]["stock_status"] == "IN_STOCK" for v in variants)
                if variants
                else item.get("stock_status") == "IN_STOCK"
            ),
            short_description=_text((item.get("short_description") or {}).get("html")),
            options=self._options(variants),
            attributes=self._attributes(item),
        )

    def _variant(self, family: Product, variant: dict[str, Any]) -> Product:
        child = variant["product"]
        price, currency = self._price(child)
        return Product(
            product_id=child["sku"],
            title=child.get("name") or family.title,
            price=price,
            currency=currency,
            image_url=(child.get("small_image") or {}).get("url") or family.image_url,
            in_stock=child["stock_status"] == "IN_STOCK",
            option_values={a["code"]: a["label"] for a in variant.get("attributes") or []},
            variant_of=family.product_id,
            attributes=self._attributes(child),
        )

    # -- Catalog ------------------------------------------------------------------

    async def search_products(
        self,
        session: ShoppingSessionContext,
        query: str,
        filters: SearchFilters | None = None,
        limit: int = 8,
    ) -> list[Product]:
        attributes = (filters.attributes if filters else None) or {}
        maximum = _points_bound(attributes.get("clubpoints_max"))
        minimum = _points_bound(attributes.get("clubpoints_min"))
        if (maximum is not None or minimum is not None) and self._overlay is not None:
            # A points-budget question: the tiles price in CP, so they answer it whole.
            return await self._overlay.budget_search(
                maximum=maximum, minimum=minimum, text=query, limit=limit
            )
        data = await self._query(SEARCH_QUERY, {"q": query, "limit": limit}, "SearchProducts")
        products = [self._summary(item) for item in data["products"]["items"]]
        return [await self._enrich(product) for product in products]

    async def get_product_details(
        self, session: ShoppingSessionContext, product_id: str
    ) -> ProductDetails | None:
        data = await self._query(DETAIL_QUERY, {"sku": product_id}, "ProductDetail")
        # A variant's own sku resolves to it; refuse near-matches Magento may return.
        items = [i for i in data["products"]["items"] if i["sku"] == product_id]
        if not items:
            return None
        item = items[0]
        summary = await self._enrich(self._summary(item))
        details = ProductDetails(
            **summary.model_dump()
            | {
                "long_description": _text((item.get("description") or {}).get("html")),
                "specs": {"manufacturer": item["manufacturer"]} if item.get("manufacturer") else {},
                "rating": round(item.get("rating_summary") or 0) / 20,  # Magento: 0-100
                "review_count": item.get("review_count"),
            }
        )
        details.variants = [
            await self._enrich(self._variant(details, variant))
            for variant in item.get("variants") or []
        ]
        return details

    async def get_account_context(self, session: ShoppingSessionContext) -> dict[str, Any] | None:
        # The dynamic context block: what a member's session states until the Phase 2b
        # token replaces the demo knobs with the real tier and points balance.
        if self._demo_tier is None and self._demo_clubpoints is None:
            return None
        context: dict[str, Any] = {"member": "demo stand-in until the member token lands"}
        if self._demo_tier is not None:
            context["tier"] = self._demo_tier
        if self._demo_clubpoints is not None:
            context["clubpoints_balance"] = f"{self._demo_clubpoints:,}"
        return context

    # -- Guest context ------------------------------------------------------------

    async def get_preferences(self, session: ShoppingSessionContext) -> UserPreferences:
        # Read before every turn, so it answers for guests: no member token until
        # Phase 2b, hence no display name beyond the demo knobs.
        return UserPreferences(
            user_id=session.user_id,
            loyalty_tier=self._demo_tier,
            preferences={"store": self._store_code},
        )

    # -- Not wired yet ------------------------------------------------------------

    async def get_cart(self, session: ShoppingSessionContext):
        raise _not_wired("cart", "Phase 3")

    async def add_to_cart(self, session: ShoppingSessionContext, product_id: str, quantity: int):
        raise _not_wired("cart", "Phase 3")

    async def update_cart_item(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ):
        raise _not_wired("cart", "Phase 3")

    async def remove_from_cart(self, session: ShoppingSessionContext, product_id: str):
        raise _not_wired("cart", "Phase 3")

    async def get_orders(self, session: ShoppingSessionContext, limit: int = 5):
        raise _not_wired("order history", "Phase 3")

    async def get_order(self, session: ShoppingSessionContext, order_id: str):
        raise _not_wired("order history", "Phase 3")

    async def search_policies(self, session: ShoppingSessionContext, query: str):
        raise _not_wired("policy library", "Phase 2")

    async def get_fulfillment_options(
        self, session: ShoppingSessionContext, product_ids: list[str]
    ):
        raise _not_wired("fulfillment", "Phase 3")
