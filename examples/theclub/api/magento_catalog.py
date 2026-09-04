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

import base64
import html
import logging
import re
import time
from typing import Any

import httpx

from shopping_agent import (
    Cart,
    CartItem,
    CheckoutHandoff,
    Product,
    ProductDetails,
    SearchFilters,
    ShoppingSessionContext,
    StorefrontBackend,
    Unavailable,
    UserPreferences,
)

from .aem_price_overlay import AemPriceOverlay

_logger = logging.getLogger("theclub.magento")

# The checkout handoff may need a product page the turn never read; this context is
# only for that catalog lookup — it carries no member identity either way.
_url_lookup_session = ShoppingSessionContext(session_id="url-lookup", user_id="catalog")

DEFAULT_GRAPHQL_URL = "https://shop.theclub.com.hk/graphql"
DEFAULT_STORE = "en_US"
SHOP_BASE = "https://shop.theclub.com.hk"
CART_PAGE_URL = f"{SHOP_BASE}/checkout/cart/"

SEARCH_QUERY = """
query SearchProducts($q: String!, $limit: Int!) {
  products(search: $q, pageSize: $limit) {
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

# The guest cart, a real quote on the shop: created per session, priced in HKD cash.
# A member-priced redemption (a CR- line at 0) is a phantom in a guest cart and is
# refused at add time and dropped on read.
CREATE_CART_MUTATION = "mutation CreateEmptyCart { createEmptyCart }"

CART_QUERY = """
query Cart($id: String!) {
  cart(cart_id: $id) {
    total_quantity
    prices { grand_total { value currency } }
    items {
      uid
      product { sku name }
      quantity
      prices {
        price { value currency }
        row_total { value currency }
      }
    }
  }
}
"""

ADD_TO_CART_MUTATION = """
mutation AddProductsToCart($id: String!, $items: [CartItemInput!]!) {
  addProductsToCart(cartId: $id, cartItems: $items) {
    user_errors { code message }
    cart { total_quantity }
  }
}
"""

UPDATE_CART_MUTATION = """
mutation UpdateCartItems($input: UpdateCartItemsInput!) {
  updateCartItems(input: $input) {
    cart { total_quantity }
  }
}
"""

REMOVE_CART_MUTATION = """
mutation RemoveItemFromCart($input: RemoveItemFromCartInput!) {
  removeItemFromCart(input: $input) {
    cart { total_quantity }
  }
}
"""


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


def _item_id(uid: str) -> int | None:
    """The shop's line uid is its quote-item id base64-encoded; its update and remove
    mutations take the raw integer."""
    try:
        return int(base64.b64decode(uid).decode())
    except (ValueError, UnicodeDecodeError):
        return None


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
        # What a cart write or the checkout handoff needs from earlier reads: the
        # session's guest quote, a variant's family, the product page, the vendor.
        self._carts: dict[str, str] = {}
        self._cart_lines: dict[str, dict[str, str]] = {}  # quote -> written id -> line uid
        self._variant_parents: dict[str, str] = {}
        self._product_urls: dict[str, str] = {}
        self._vendors: dict[str, str] = {}

    def _remember(self, products: list[Product]) -> None:
        for product in products:
            if url := product.attributes.get("url"):
                self._product_urls[product.product_id] = url
            if vendor := product.attributes.get("vendor"):
                self._vendors[product.product_id] = vendor
            if product.variant_of:
                self._variant_parents[product.product_id] = product.variant_of

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
        attributes = self._attributes(item)
        if url_key := item.get("url_key"):
            attributes["url"] = f"{SHOP_BASE}/{url_key}"
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
            attributes=attributes,
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
            products = await self._overlay.budget_search(
                maximum=maximum, minimum=minimum, text=query, limit=limit
            )
            self._remember(products)
            return products
        data = await self._query(SEARCH_QUERY, {"q": query, "limit": limit}, "SearchProducts")
        products = [self._summary(item) for item in data["products"]["items"]]
        products = [await self._enrich(product) for product in products]
        self._remember(products)
        return products

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
        self._remember([details, *details.variants])
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

    # -- Cart: a real guest quote on the shop, cash lines only -------------------

    async def _cart_id(self, session: ShoppingSessionContext) -> str:
        if cart_id := self._carts.get(session.session_id):
            return cart_id
        data = await self._query(CREATE_CART_MUTATION, {}, "CreateEmptyCart")
        cart_id = data["createEmptyCart"]
        self._carts[session.session_id] = cart_id
        return cart_id

    async def _raw_cart(self, cart_id: str) -> dict[str, Any]:
        data = await self._query(CART_QUERY, {"id": cart_id}, "Cart")
        return data["cart"]

    async def _read_cart(self, cart_id: str, raw: dict[str, Any] | None = None) -> Cart:
        raw = raw if raw is not None else await self._raw_cart(cart_id)
        # The shop's cart lines name a configurable by its family and expose no child
        # identity (their schema strips it), so the ids the model writes are tracked
        # against line uids by the adds that put them there.
        mapping = self._cart_lines.setdefault(cart_id, {})
        live = {line["uid"] for line in raw["items"]}
        for ours, uid in list(mapping.items()):
            if uid not in live:
                del mapping[ours]  # the line left; its id is unmapped
        items: list[CartItem] = []
        phantom: list[tuple[str, str]] = []
        for line in raw["items"]:
            sku = (
                next((ours for ours, uid in mapping.items() if uid == line["uid"]), None)
                or line["product"]["sku"]
            )
            if float(line["prices"]["price"]["value"]) == 0.0 and sku.startswith("CR-"):
                # A redemption the guest cannot price: a phantom line, not an offer.
                phantom.append((line["uid"], sku))
                continue
            items.append(
                CartItem(
                    product_id=sku,
                    title=line["product"]["name"],
                    price=float(line["prices"]["price"]["value"]),
                    quantity=int(line["quantity"]),
                )
            )
        for uid, sku in phantom:
            _logger.info("theclub: dropping member-priced redemption %s from the guest cart", sku)
            if item_id := _item_id(uid):
                await self._remove_line(cart_id, item_id)
        return Cart(items=items, currency="HKD")

    async def _line_uid(self, cart_id: str, product_id: str) -> str | None:
        mapping = self._cart_lines.get(cart_id, {})
        if uid := mapping.get(product_id):
            return uid
        raw = await self._raw_cart(cart_id)
        for line in raw["items"]:  # simple products: the line names them itself
            if line["product"]["sku"] == product_id and line["uid"] not in mapping.values():
                return line["uid"]
        return None

    async def get_cart(self, session: ShoppingSessionContext) -> Cart:
        if not (cart_id := self._carts.get(session.session_id)):
            return Cart(currency="HKD")
        return await self._read_cart(cart_id)

    async def add_to_cart(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        if product_id.startswith("CR-"):
            raise Unavailable(
                f"{product_id} is a Clubpoints redemption: its points price and its "
                "checkout are member-only. Sign in on shop.theclub.com.hk to redeem it."
            )
        cart_id = await self._cart_id(session)
        before = {
            line["uid"]: int(line["quantity"]) for line in (await self._raw_cart(cart_id))["items"]
        }
        item: dict[str, Any] = {"sku": product_id, "quantity": quantity}
        if parent := self._variant_parents.get(product_id):
            item["parent_sku"] = parent  # configurables sell their variants under a family
        data = await self._query(
            ADD_TO_CART_MUTATION, {"id": cart_id, "items": [item]}, "AddProductsToCart"
        )
        if errors := data["addProductsToCart"]["user_errors"]:
            raise Unavailable(f"{product_id}: {'; '.join(e['message'] for e in errors)}")
        # Whichever line appeared or grew is the one just added; name it by the id written.
        after = await self._raw_cart(cart_id)
        mapping = self._cart_lines.setdefault(cart_id, {})
        for line in after["items"]:
            grew = line["uid"] not in before or int(line["quantity"]) > before.get(line["uid"], 0)
            if grew and line["uid"] not in mapping.values():
                mapping[product_id] = line["uid"]
        return await self._read_cart(cart_id, after)

    async def _remove_line(self, cart_id: str, item_id: int) -> None:
        await self._query(
            REMOVE_CART_MUTATION,
            {"input": {"cart_id": cart_id, "cart_item_id": item_id}},
            "RemoveItemFromCart",
        )

    async def update_cart_item(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        cart_id = self._carts.get(session.session_id)
        uid = await self._line_uid(cart_id, product_id) if cart_id else None
        item_id = _item_id(uid) if uid else None
        if cart_id and item_id:
            await self._query(
                UPDATE_CART_MUTATION,
                {
                    "input": {
                        "cart_id": cart_id,
                        "cart_items": [{"cart_item_id": item_id, "quantity": quantity}],
                    }
                },
                "UpdateCartItems",
            )
            return await self._read_cart(cart_id)
        return await self.get_cart(session)  # not in the cart: leave it as it is

    async def remove_from_cart(self, session: ShoppingSessionContext, product_id: str) -> Cart:
        cart_id = self._carts.get(session.session_id)
        uid = await self._line_uid(cart_id, product_id) if cart_id else None
        item_id = _item_id(uid) if uid else None
        if cart_id and item_id:
            await self._remove_line(cart_id, item_id)
            self._cart_lines.get(cart_id, {}).pop(product_id, None)
            return await self._read_cart(cart_id)
        return await self.get_cart(session)

    async def checkout_handoff(
        self, session: ShoppingSessionContext, cart: Cart
    ) -> list[CheckoutHandoff]:
        """Where the staged cart is completed: the site itself, never this agent. The
        member's browser cannot resume this server-side guest quote (a different
        session), so the handoff is the shop's sign-in page plus each line's own
        product page — the vendor split where the vendor is known."""
        if not cart.items:
            return []
        handoffs = [CheckoutHandoff(url=CART_PAGE_URL, label="Sign in on The Club")]
        for item in cart.items:
            if url := await self._product_url(item.product_id):
                handoffs.append(
                    CheckoutHandoff(
                        url=url,
                        label=f"Buy {item.title[:40]}",
                        seller=self._vendors.get(item.product_id),
                    )
                )
        return handoffs[:9]

    async def _product_url(self, product_id: str) -> str | None:
        if url := self._product_urls.get(product_id):
            return url
        # A variant's page is its family's page in this catalog.
        family = self._variant_parents.get(product_id)
        if family and (url := self._product_urls.get(family)):
            return url
        details = await self.get_product_details(_url_lookup_session, product_id)
        return self._product_urls.get(product_id) if details else None

    # -- Not wired yet ------------------------------------------------------------

    async def get_orders(self, session: ShoppingSessionContext, limit: int = 5):
        raise _not_wired("order history", "after the member token")

    async def get_order(self, session: ShoppingSessionContext, order_id: str):
        raise _not_wired("order history", "after the member token")

    async def search_policies(self, session: ShoppingSessionContext, query: str):
        raise _not_wired("policy library", "later")

    async def get_fulfillment_options(
        self, session: ShoppingSessionContext, product_ids: list[str]
    ):
        raise _not_wired("fulfillment", "later")
