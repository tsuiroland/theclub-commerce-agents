# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""MultiStoreBackend over two scripted stores: namespaced ids, round-robin fan-out
merge, raw-id fallback, per-line store cart routing, and a store outage degrading
to the other store's answer."""

from __future__ import annotations

from datetime import datetime

from shopping_agent import (
    Cart,
    CartItem,
    CheckoutHandoff,
    NotOffered,
    Order,
    OrderStatus,
    Product,
    ProductDetails,
    ShoppingSessionContext,
    StorefrontBackend,
    UserPreferences,
)

from ..multi_store import MultiStoreBackend

SESSION = ShoppingSessionContext(session_id="s-1", user_id="u-1")


def product(product_id: str, title: str = "", **extra: object) -> Product:
    return Product(
        product_id=product_id,
        title=title or product_id,
        price=100.0,
        attributes={"store": "should be overwritten"},
        **extra,  # type: ignore[arg-type]
    )


class ScriptedStore(StorefrontBackend):
    """A store with fixed search results, a details record per known id, and an
    in-memory cart; ``fail`` turns its search into an outage."""

    store_name = "Scripted"

    def __init__(
        self,
        results: list[Product],
        known: dict[str, ProductDetails],
        orders: list[Order] | None = None,
    ) -> None:
        self._results = results
        self._known = known
        self._orders = orders or []
        self.cart = Cart(currency="HKD")
        self.added: list[tuple[str, int]] = []
        self.fail = False

    async def search_products(self, session, query, filters=None, limit=8):
        if self.fail:
            raise RuntimeError("outage")
        return self._results[:limit]

    async def get_product_details(self, session, product_id):
        return self._known.get(product_id)

    async def get_cart(self, session) -> Cart:
        return self.cart

    async def add_to_cart(self, session, product_id, quantity) -> Cart:
        self.added.append((product_id, quantity))
        self.cart = Cart(
            items=[
                CartItem(product_id=product_id, title=product_id, price=100.0, quantity=quantity)
            ],
            currency="HKD",
        )
        return self.cart

    async def update_cart_item(self, session, product_id, quantity) -> Cart:
        return self.cart

    async def remove_from_cart(self, session, product_id) -> Cart:
        self.cart = Cart(currency="HKD")
        return self.cart

    async def checkout_handoff(self, session, cart):
        return [CheckoutHandoff(url=f"https://example.com/{self.store_name}")]

    async def get_preferences(self, session) -> UserPreferences:
        return UserPreferences(user_id=session.user_id, preferences={"store": self.store_name})

    async def get_orders(self, session, limit=5):
        return self._orders[:limit]

    async def get_order(self, session, order_id):
        return next((o for o in self._orders if o.order_id == order_id), None)

    async def search_policies(self, session, query):
        return []

    async def get_fulfillment_options(self, session, product_ids):
        return []


def two_stores() -> tuple[ScriptedStore, ScriptedStore, MultiStoreBackend]:
    club_variant = product("VAR-1", option_values={"Color": "Black"}, variant_of="FAM-1")
    club_family = ProductDetails(
        **product("FAM-1", "Club family", options={"Color": ["Black"]}).model_dump(),
        variants=[club_variant],
    )
    club = ScriptedStore([product("A"), product("B")], {"FAM-1": club_family})
    club.store_name = "The Club"
    hktv = ScriptedStore([product("H1"), product("H2"), product("H3")], {})
    hktv.store_name = "HKTV Mall"
    return club, hktv, MultiStoreBackend({"theclub": club, "hktv": hktv})


async def test_search_namespaces_and_alternates_stores() -> None:
    _club, _hktv, backend = two_stores()
    products = await backend.search_products(SESSION, "anything", limit=8)

    assert [p.product_id for p in products] == [
        "theclub:A",
        "hktv:H1",
        "theclub:B",
        "hktv:H2",
        "hktv:H3",
    ]  # round-robin: the stores alternate
    assert products[0].attributes["store"] == "The Club"
    assert products[1].attributes["store"] == "HKTV Mall"


async def test_details_route_by_prefix_and_namespace_variants() -> None:
    _club, _hktv, backend = two_stores()
    details = await backend.get_product_details(SESSION, "theclub:FAM-1")

    assert details is not None
    assert details.product_id == "theclub:FAM-1"
    assert details.variants[0].product_id == "theclub:VAR-1"
    assert details.variants[0].variant_of == "theclub:FAM-1"
    assert details.attributes["store"] == "The Club"


async def test_a_raw_id_falls_back_to_the_stores_in_order() -> None:
    _club, hktv, backend = two_stores()
    hktv._known["H1"] = ProductDetails(**product("H1").model_dump())
    details = await backend.get_product_details(SESSION, "H1")

    assert details is not None  # not namespaced: the stores are tried in order
    assert details.product_id == "hktv:H1"


async def test_one_store_down_degrades_to_the_other() -> None:
    club, _hktv, backend = two_stores()
    club.fail = True
    products = await backend.search_products(SESSION, "anything")

    assert [p.product_id for p in products] == ["hktv:H1", "hktv:H2", "hktv:H3"]


async def test_cart_lines_carry_their_store_and_writes_route() -> None:
    club, _hktv, backend = two_stores()
    await backend.add_to_cart(SESSION, "theclub:A", 2)

    assert club.added == [("A", 2)]  # the store received the raw id
    cart = await backend.get_cart(SESSION)
    assert [item.product_id for item in cart.items] == ["theclub:A"]


async def test_an_unrouted_write_is_refused_with_the_reason() -> None:
    _club, _hktv, backend = two_stores()
    try:
        await backend.add_to_cart(SESSION, "A", 1)
    except NotOffered as refused:
        assert "names no store" in str(refused)
    else:
        raise AssertionError("an unprefixed write must be refused")


async def test_checkout_hands_off_only_stores_with_lines() -> None:
    club, _hktv, backend = two_stores()
    assert await backend.checkout_handoff(SESSION, Cart(currency="HKD")) == []
    await backend.add_to_cart(SESSION, "hktv:H1", 1)
    handoffs = await backend.checkout_handoff(SESSION, Cart(currency="HKD"))
    assert len(handoffs) == 1 and handoffs[0].url.endswith("/HKTV Mall")


async def test_the_primary_answers_preferences_and_orders() -> None:
    club_order = Order(
        order_id="CLUB-1",
        status=OrderStatus.DELIVERED,
        placed_at=datetime(2026, 9, 1),
        total=1.0,
        currency="HKD",
    )
    hktv_order = Order(
        order_id="HKTV-1",
        status=OrderStatus.PROCESSING,
        placed_at=datetime(2026, 9, 2),
        total=2.0,
        currency="HKD",
    )
    club = ScriptedStore([], {}, orders=[club_order])
    club.store_name = "The Club"
    hktv = ScriptedStore([], {}, orders=[hktv_order])
    hktv.store_name = "HKTV Mall"
    backend = MultiStoreBackend({"theclub": club, "hktv": hktv})

    preferences = await backend.get_preferences(SESSION)
    assert preferences.preferences["store"] == "The Club"  # the primary's own
    assert preferences.preferences["stores"] == "theclub hktv"  # plus the federation
    orders = await backend.get_orders(SESSION)
    assert [o.order_id for o in orders] == ["CLUB-1"]  # the primary's, not hktv's
