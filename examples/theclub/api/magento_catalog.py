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
from datetime import datetime
from typing import Any

import httpx

from shopping_agent import (
    Cart,
    CartItem,
    CheckoutHandoff,
    Order,
    OrderStatus,
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

# The member's own account, when one is configured: the shop's customer-token login.
# The token lives in memory for the process; the password is never logged (see _query)
# and both arrive only through the local environment.
GENERATE_TOKEN_MUTATION = """
mutation GenerateCustomerToken($email: String!, $password: String!) {
  generateCustomerToken(email: $email, password: $password) {
    token
  }
}
"""

CUSTOMER_CART_QUERY = """
query CustomerCart {
  customerCart {
    id
    total_quantity
  }
}
"""

CUSTOMER_QUERY = """
query Customer {
  customer {
    firstname
    lastname
    email
    group_id
    reward_points {
      balance {
        points
        money { value currency }
      }
    }
  }
}
"""

CUSTOMER_ORDERS_QUERY = """
query CustomerOrders {
  customer {
    orders {
      items {
        order_number
        order_date
        status
        total {
          grand_total { value currency }
        }
      }
    }
  }
}
"""

# The shop has no tier-named field anywhere (probed: tier/club_tier/loyalty_tier/
# membership all absent). If tiers map to Magento customer groups, the mapping is
# pinned here once a signed-in member_check run prints the group id; until then a
# signed-in member carries their real Clubpoints balance and no tier guess.
TIER_GROUPS: dict[int, str] = {}

# The member profile — name, customer group, loyalty balance — refreshes lazily, at
# most this often (the agent never places an order, but the site may between turns).
PROFILE_TTL_SECONDS = 300.0


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


def _needs_reauth(errors: list[dict[str, Any]]) -> bool:
    """Whether an error batch is only the token expiring, worth one fresh sign-in."""
    text = " ".join(str(error.get("message", "")).lower() for error in errors)
    category = {str(error.get("extensions", {}).get("category", "")) for error in errors}
    return "graphql-authorization" in category or "isn't authorized" in text


def _order_status(status: str) -> OrderStatus:
    """The shop's order-status wording onto the enum, processing as the fallback."""
    for candidate in OrderStatus:
        if candidate.value == status:
            return candidate
    return OrderStatus.PROCESSING


class ClubMagentoCatalog(StorefrontBackend):
    """Reads shop.theclub.com.hk's GraphQL storefront, its results enriched by the AEM
    points overlay when one is attached. One async client for the process. With the
    member's own credentials the shop's customer token carries their profile, their
    Clubpoints balance, their cart, and their orders; without it every method acts for
    the session as a guest, with ``demo_tier`` and ``demo_clubpoints`` standing in for
    the member context."""

    def __init__(
        self,
        graphql_url: str = DEFAULT_GRAPHQL_URL,
        store_code: str = DEFAULT_STORE,
        client: httpx.AsyncClient | None = None,
        overlay: AemPriceOverlay | None = None,
        demo_tier: str | None = None,
        demo_clubpoints: int | None = None,
        email: str | None = None,
        password: str | None = None,
        token: str | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=15.0)
        self._graphql_url = graphql_url
        self._store_code = store_code
        self._overlay = overlay
        self._demo_tier = demo_tier
        self._demo_clubpoints = demo_clubpoints
        self._email = email
        self._password = password
        # A harvested bearer token signs the member in directly — the shop's SSO
        # provisioned accounts carry no local password to log in with (see
        # member_check); the token rides .env like the credentials do.
        self._token = token
        self._member_cart_id: str | None = None
        self._profile_cache: tuple[float, dict[str, Any]] | None = None
        # What a cart write or the checkout handoff needs from earlier reads: the
        # session's guest quote, a variant's family, the product page, the vendor.
        self._carts: dict[str, str] = {}
        self._cart_lines: dict[str, dict[str, str]] = {}  # quote -> written id -> line uid
        self._variant_parents: dict[str, str] = {}
        self._product_urls: dict[str, str] = {}
        self._vendors: dict[str, str] = {}

    @property
    def member_mode(self) -> bool:
        """True when the deployment carries the member's own Club credentials or a
        harvested member token."""
        return (self._email is not None and self._password is not None) or self._token is not None

    @property
    def _member_label(self) -> str:
        return self._email or "member (bearer token)"

    async def _ensure_token(self) -> str | None:
        if not self.member_mode:
            return None
        if self._token is None:
            data = await self._query(
                GENERATE_TOKEN_MUTATION,
                {"email": self._email, "password": self._password},
                "GenerateCustomerToken",
            )
            self._token = data["generateCustomerToken"]["token"]
            _logger.info("theclub: member signed in (%s)", self._email)
        return self._token

    async def _customer_profile(self) -> dict[str, Any]:
        """The member's own profile — name, customer group, the loyalty balance the
        shop's RewardPoints module carries — fetched at most once per few minutes."""
        now = time.monotonic()
        if self._profile_cache and now - self._profile_cache[0] < PROFILE_TTL_SECONDS:
            return self._profile_cache[1]
        data = await self._query(CUSTOMER_QUERY, {}, "Customer", authed=True)
        self._profile_cache = (now, data["customer"])
        return data["customer"]

    @staticmethod
    def _loyalty(customer: dict[str, Any]) -> tuple[str | None, int | None]:
        """(tier, Clubpoints balance) from the member profile: the balance exactly as
        the shop reports it, the tier only when the customer group maps to one."""
        balance = (customer.get("reward_points") or {}).get("balance") or {}
        return TIER_GROUPS.get(customer.get("group_id")), balance.get("points")

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
        self,
        query: str,
        variables: dict[str, Any],
        operation_name: str,
        authed: bool = False,
    ) -> dict[str, Any]:
        token = await self._ensure_token() if authed else None
        headers = {"Content-Type": "application/json", "Store": self._store_code}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        started = time.monotonic()
        response = await self._client.post(
            self._graphql_url,
            json={"query": query, "variables": variables, "operationName": operation_name},
            headers=headers,
        )
        _logger.info(
            "theclub <- Magento GraphQL %s %s (%.0f ms)",
            operation_name,
            {key: "***" if key == "password" else value for key, value in variables.items()},
            (time.monotonic() - started) * 1000,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):  # Magento answers 200 with per-field errors
            if authed and self._token and _needs_reauth(body["errors"]):
                self._token = None  # expired mid-process: sign in again, once
                return await self._query(query, variables, operation_name, authed=True)
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
        # The dynamic context block: the member's own session when signed in — their
        # real balance, and their tier once the group mapping is pinned — the demo
        # knobs when not (a guest deployment runs with neither and returns None).
        if self.member_mode:
            customer = await self._customer_profile()
            tier, points = self._loyalty(customer)
            context: dict[str, Any] = {"member": self._member_label, "signed_in": True}
            if tier is not None:
                context["tier"] = tier
            if points is not None:
                context["clubpoints_balance"] = f"{points:,}"
            return context
        if self._demo_tier is None and self._demo_clubpoints is None:
            return None
        context = {"member": "demo stand-in until the member token lands"}
        if self._demo_tier is not None:
            context["tier"] = self._demo_tier
        if self._demo_clubpoints is not None:
            context["clubpoints_balance"] = f"{self._demo_clubpoints:,}"
        return context

    async def get_preferences(self, session: ShoppingSessionContext) -> UserPreferences:
        # Read before every turn; the signed-in member states their own name.
        if self.member_mode:
            customer = await self._customer_profile()
            tier, _ = self._loyalty(customer)
            return UserPreferences(
                user_id=session.user_id,
                display_name=customer.get("firstname") or self._member_label,
                loyalty_tier=tier,
                preferences={"store": self._store_code, "member_email": self._email or ""},
            )
        return UserPreferences(
            user_id=session.user_id,
            loyalty_tier=self._demo_tier,
            preferences={"store": self._store_code},
        )

    # -- Cart: the member's own quote, or a guest quote on the shop --------------

    async def _cart_id(self, session: ShoppingSessionContext) -> str:
        if cart_id := self._carts.get(session.session_id):
            return cart_id
        if self.member_mode:
            # The signed-in member has one cart of their own on the shop.
            data = await self._query(CUSTOMER_CART_QUERY, {}, "CustomerCart", authed=True)
            cart_id = data["customerCart"]["id"]
            self._member_cart_id = cart_id
            self._carts[session.session_id] = cart_id
            return cart_id
        data = await self._query(CREATE_CART_MUTATION, {}, "CreateEmptyCart")
        cart_id = data["createEmptyCart"]
        self._carts[session.session_id] = cart_id
        return cart_id

    def _authed_cart(self, cart_id: str) -> bool:
        return cart_id == self._member_cart_id

    async def _raw_cart(self, cart_id: str) -> dict[str, Any]:
        data = await self._query(
            CART_QUERY, {"id": cart_id}, "Cart", authed=self._authed_cart(cart_id)
        )
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
        if self.member_mode:
            return await self._read_cart(await self._cart_id(session))
        if not (cart_id := self._carts.get(session.session_id)):
            return Cart(currency="HKD")
        return await self._read_cart(cart_id)

    async def add_to_cart(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        if product_id.startswith("CR-") and not self.member_mode:
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
            ADD_TO_CART_MUTATION,
            {"id": cart_id, "items": [item]},
            "AddProductsToCart",
            authed=self._authed_cart(cart_id),
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
            authed=self._authed_cart(cart_id),
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
                authed=self._authed_cart(cart_id),
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

    # -- Orders: the member's own; a guest has none to read -----------------------

    async def get_orders(self, session: ShoppingSessionContext, limit: int = 5) -> list[Order]:
        if not self.member_mode:
            raise _not_wired("order history", "after the member token")
        # Field names follow the shop's customer-order shape as probed; anything the
        # shop omits reads as absent rather than failing the whole history.
        data = await self._query(CUSTOMER_ORDERS_QUERY, {}, "CustomerOrders", authed=True)
        orders = []
        for raw in (data["customer"].get("orders") or {}).get("items") or []:
            total = (raw.get("total") or {}).get("grand_total") or {}
            orders.append(
                Order(
                    order_id=str(raw.get("order_number") or ""),
                    status=_order_status(str(raw.get("status") or "").lower()),
                    placed_at=datetime.fromisoformat(
                        str(raw.get("order_date") or "2026-01-01 00:00:00").replace(" ", "T")
                    ),
                    total=float(total.get("value") or 0.0),
                    currency=total.get("currency") or "HKD",
                )
            )
        return orders[:limit]

    async def get_order(self, session: ShoppingSessionContext, order_id: str) -> Order | None:
        for order in await self.get_orders(session, limit=20):
            if order.order_id == order_id:
                return order
        return None

    # -- Not wired yet ------------------------------------------------------------

    async def search_policies(self, session: ShoppingSessionContext, query: str):
        raise _not_wired("policy library", "later")

    async def get_fulfillment_options(
        self, session: ShoppingSessionContext, product_ids: list[str]
    ):
        raise _not_wired("fulfillment", "later")
