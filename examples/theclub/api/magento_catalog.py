# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The Club's live read-only catalog over shop.theclub.com.hk's Magento GraphQL — Phase 1
of this example's roadmap. Search and product details are wired; The Club's other
systems exist but are not wired yet, so those methods raise and their tools answer that
they are temporarily unavailable (the config keeps their switches on, per the backend
contract).

Anonymous GraphQL serves the HKD cash price only: Clubpoints redemption prices are
member-gated in Magento and arrive in Phase 2 with the member token. The Club's AEM
shopping pages carry them anonymously, but only for pages AEM has merchandised, so this
backend takes ``clubpoints`` from Magento when it is served and flags pure-redemption
items (observed convention: ``CR-`` SKU prefix, HKD final price of 0) with a price note
the assistant can state plainly. Search filters beyond free text (category, price
ranges) are Phase 1.5; ``sort`` and rating filters are ignored here."""

from __future__ import annotations

import html
import re
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


def _text(fragment: str | None) -> str | None:
    """Magento serves prose as HTML; the model reads plain fenced text."""
    if not fragment:
        return None
    return _WHITESPACE.sub(" ", html.unescape(_TAGS.sub(" ", fragment))).strip() or None


class ClubMagentoCatalog(StorefrontBackend):
    """Reads shop.theclub.com.hk's anonymous GraphQL storefront. One async client for
    the process; every method acts for the session but carries no member token yet."""

    def __init__(
        self,
        graphql_url: str = DEFAULT_GRAPHQL_URL,
        store_code: str = DEFAULT_STORE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=15.0)
        self._graphql_url = graphql_url
        self._store_code = store_code

    async def _query(
        self, query: str, variables: dict[str, Any], operation_name: str
    ) -> dict[str, Any]:
        response = await self._client.post(
            self._graphql_url,
            json={"query": query, "variables": variables, "operationName": operation_name},
            headers={"Content-Type": "application/json", "Store": self._store_code},
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
        data = await self._query(SEARCH_QUERY, {"q": query, "limit": limit}, "SearchProducts")
        return [self._summary(item) for item in data["products"]["items"]]

    async def get_product_details(
        self, session: ShoppingSessionContext, product_id: str
    ) -> ProductDetails | None:
        data = await self._query(DETAIL_QUERY, {"sku": product_id}, "ProductDetail")
        # A variant's own sku resolves to it; refuse near-matches Magento may return.
        items = [i for i in data["products"]["items"] if i["sku"] == product_id]
        if not items:
            return None
        item = items[0]
        summary = self._summary(item)
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
            self._variant(details, variant) for variant in item.get("variants") or []
        ]
        return details

    # -- Guest context ------------------------------------------------------------

    async def get_preferences(self, session: ShoppingSessionContext) -> UserPreferences:
        # Read before every turn, so it answers for guests: no member token until
        # Phase 2, hence no display name, tier, or points balance to state.
        return UserPreferences(user_id=session.user_id, preferences={"store": self._store_code})

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
