# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""ClubMagentoCatalog against captured shapes of shop.theclub.com.hk's GraphQL (offline,
via httpx.MockTransport): mapping, dual-currency notes, family options, and the honest
unavailable answer every unwired system gives."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from shopping_agent import SearchFilters, ShoppingSessionContext, Unavailable

from ..aem_price_overlay import AemPriceOverlay
from ..magento_catalog import ClubMagentoCatalog

# Captured from the live endpoint (Store: en_US), trimmed to the fields the queries read.
SEARCH_RESPONSE: dict[str, Any] = {
    "data": {
        "products": {
            "items": [
                {
                    "sku": "NothingHP1",
                    "name": "Nothing Headphone (1)",
                    "stock_status": "IN_STOCK",
                    "clubpoints": 0,
                    "small_image": {"url": "https://shop.theclub.com.hk/m/hp.jpg"},
                    "categories": [{"name": "Mobile PC & Gadgets"}, {"name": "Headphone"}],
                    "price_range": {
                        "minimum_price": {
                            "regular_price": {"value": 2299},
                            "final_price": {"value": 1799, "currency": "HKD"},
                        }
                    },
                },
                {
                    "sku": "Clicks_16PM",
                    "name": "Clicks keyboard for iphone 16 Pro Max",
                    "stock_status": "IN_STOCK",
                    "clubpoints": 0,
                    "small_image": {"url": "https://shop.theclub.com.hk/m/clicks.jpg"},
                    "categories": [{"name": "Mobile PC & Gadgets"}],
                    "price_range": {
                        "minimum_price": {
                            "regular_price": {"value": 368},
                            "final_price": {"value": 368, "currency": "HKD"},
                        }
                    },
                    "variants": [
                        {
                            "attributes": [{"code": "color", "label": "Spice", "value_index": 1}],
                            "product": {
                                "sku": "4188051",
                                "stock_status": "IN_STOCK",
                                "price_range": {
                                    "minimum_price": {
                                        "final_price": {"value": 368, "currency": "HKD"}
                                    }
                                },
                            },
                        },
                        {
                            "attributes": [{"code": "color", "label": "Surf", "value_index": 2}],
                            "product": {
                                "sku": "4188061",
                                "stock_status": "OUT_OF_STOCK",
                                "price_range": {
                                    "minimum_price": {
                                        "final_price": {"value": 368, "currency": "HKD"}
                                    }
                                },
                            },
                        },
                    ],
                },
                {
                    "sku": "CR-MANN-50-26B1",
                    "name": "Mannings - HK$50 e-Gift Voucher",
                    "stock_status": "IN_STOCK",
                    "clubpoints": 0,  # anonymous: the points price is member-gated
                    "small_image": {"url": "https://shop.theclub.com.hk/m/mannings.jpg"},
                    "categories": [{"name": "Rewards"}],
                    "price_range": {
                        "minimum_price": {
                            "regular_price": {"value": 0},
                            "final_price": {"value": 0, "currency": "HKD"},
                        }
                    },
                },
            ]
        }
    }
}

DETAIL_RESPONSE: dict[str, Any] = {
    "data": {
        "products": {
            "items": [
                {
                    "sku": "Clicks_16PM",
                    "name": "Clicks keyboard for iphone 16 Pro Max",
                    "url_key": "clicks-keyboard-iphone-16-pro-max",
                    "stock_status": "IN_STOCK",
                    "clubpoints": 0,
                    "small_image": {"url": "https://shop.theclub.com.hk/m/clicks.jpg"},
                    "categories": [{"name": "Mobile PC & Gadgets"}],
                    "price_range": {
                        "minimum_price": {
                            "regular_price": {"value": 368},
                            "final_price": {"value": 368, "currency": "HKD"},
                        }
                    },
                    "manufacturer": "Clicks",
                    "rating_summary": 80,
                    "review_count": 12,
                    "description": {"html": "<p>Keys for the iPhone.</p>"},
                    "short_description": {"html": "<ol><li>A keyboard.</li></ol>"},
                    "variants": [
                        {
                            "attributes": [{"code": "color", "label": "Spice", "value_index": 1}],
                            "product": {
                                "sku": "4188051",
                                "name": "Clicks keyboard for iphone 16 Pro Max (Spice)",
                                "stock_status": "IN_STOCK",
                                "clubpoints": 0,
                                "small_image": {"url": "https://shop.theclub.com.hk/m/spice.jpg"},
                                "price_range": {
                                    "minimum_price": {
                                        "final_price": {"value": 368, "currency": "HKD"}
                                    }
                                },
                            },
                        }
                    ],
                }
            ]
        }
    }
}

EMPTY_RESPONSE = {"data": {"products": {"items": []}}}


@pytest.fixture
def requests() -> list[httpx.Request]:
    return []


@pytest.fixture
def backend(requests: list[httpx.Request]) -> ClubMagentoCatalog:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        name = json.loads(request.content)["operationName"]
        body = (
            SEARCH_RESPONSE
            if name == "SearchProducts"
            else DETAIL_RESPONSE
            if name == "ProductDetail"
            else EMPTY_RESPONSE
        )
        return httpx.Response(200, json=body)

    return ClubMagentoCatalog(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.fixture
def session() -> ShoppingSessionContext:
    return ShoppingSessionContext(session_id="s1", user_id="u1")


async def test_search_maps_plain_product(
    backend: ClubMagentoCatalog, session: ShoppingSessionContext
) -> None:
    products = await backend.search_products(session, "keyboard")
    assert len(products) == 3
    plain = products[0]
    assert plain.product_id == "NothingHP1"
    assert plain.price == 1799.0
    assert plain.currency == "HKD"
    assert plain.in_stock
    assert plain.category == "Mobile PC & Gadgets"
    assert not plain.has_options


async def test_search_family_carries_options(
    backend: ClubMagentoCatalog, session: ShoppingSessionContext
) -> None:
    family = (await backend.search_products(session, "keyboard"))[1]
    assert family.has_options
    assert family.options == {"color": ["Spice", "Surf"]}
    assert family.in_stock  # any variant in stock


async def test_rewards_item_states_member_pricing(
    backend: ClubMagentoCatalog, session: ShoppingSessionContext
) -> None:
    reward = (await backend.search_products(session, "keyboard"))[2]
    assert reward.attributes["catalog"] == "rewards"
    assert "member login" in reward.attributes["price_note"]
    assert reward.price == 0.0


async def test_detail_variants_and_specs(
    backend: ClubMagentoCatalog, session: ShoppingSessionContext
) -> None:
    details = await backend.get_product_details(session, "Clicks_16PM")
    assert details is not None
    assert details.specs == {"manufacturer": "Clicks"}
    assert details.rating == 4.0  # Magento's 0-100 summary, halved to the 0-5 scale
    assert details.review_count == 12
    assert "<" not in (details.long_description or "")
    assert len(details.variants) == 1
    variant = details.variants[0]
    assert variant.product_id == "4188051"
    assert variant.option_values == {"color": "Spice"}
    assert variant.variant_of == "Clicks_16PM"
    assert variant.price == 368.0


async def test_unknown_sku_is_none(
    backend: ClubMagentoCatalog, session: ShoppingSessionContext
) -> None:
    assert await backend.get_product_details(session, "NOPE") is None


async def test_guest_preferences(
    backend: ClubMagentoCatalog, session: ShoppingSessionContext
) -> None:
    preferences = await backend.get_preferences(session)
    assert preferences.user_id == "u1"
    assert preferences.loyalty_tier is None  # no member token until Phase 2


async def test_unwired_systems_answer_unavailable(
    backend: ClubMagentoCatalog, session: ShoppingSessionContext
) -> None:
    with pytest.raises(RuntimeError):
        await backend.get_orders(session)
    with pytest.raises(RuntimeError):
        await backend.search_policies(session, "refund")
    with pytest.raises(RuntimeError):
        await backend.get_fulfillment_options(session, ["NothingHP1"])


async def test_store_header_and_url(
    backend: ClubMagentoCatalog, requests: list[httpx.Request], session: ShoppingSessionContext
) -> None:
    from ..magento_catalog import DEFAULT_GRAPHQL_URL

    await backend.search_products(session, "keyboard")
    (request,) = requests
    assert str(request.url) == DEFAULT_GRAPHQL_URL
    assert request.headers["Store"] == "en_US"


def test_default_endpoint_points_at_the_club() -> None:
    from ..magento_catalog import DEFAULT_GRAPHQL_URL

    assert DEFAULT_GRAPHQL_URL == "https://shop.theclub.com.hk/graphql"


# -- The Phase 2 overlay, through the catalog --------------------------------------------

OVERLAY_MODEL = {
    ":items": {
        "root": {
            ":items": {
                "grid": {
                    ":items": {
                        "tile": {
                            "sku": "NothingHP1",
                            "name": "Nothing Headphone (1)",
                            "brandLabel": "Nothing",
                            "inStock": True,
                            "priceDisplay": {
                                "crossedPrice": {"cp": None, "hkd": "2299.0"},
                                "finalPrice": {"cp": "5000", "hkd": None},
                            },
                            "minClubPoints": 5000,
                            "originalPrice": 2299.0,
                            "extraClubPoints": 300,
                            "subCat1": "Rewards",
                            "rewardsOrShopping": "Rewards",
                            "vendorName": "digilife",
                        },
                        "voucher": {
                            "sku": "CR-WEL-100-26B5",
                            "name": "Wellcome - HK$100 E-Shopping Voucher",
                            "inStock": True,
                            "priceDisplay": {
                                "crossedPrice": {"cp": None, "hkd": "100.0"},
                                "finalPrice": {"cp": "690", "hkd": None},
                            },
                            "minClubPoints": 690,
                            "rewardsOrShopping": "Rewards",
                            "subCat1": "Rewards",
                        },
                    }
                }
            }
        }
    }
}


@pytest.fixture
def overlaid(requests: list[httpx.Request]) -> ClubMagentoCatalog:
    def graphql(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        name = json.loads(request.content)["operationName"]
        body = SEARCH_RESPONSE if name == "SearchProducts" else EMPTY_RESPONSE
        return httpx.Response(200, json=body)

    def aem(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=OVERLAY_MODEL)

    return ClubMagentoCatalog(
        client=httpx.AsyncClient(transport=httpx.MockTransport(graphql)),
        overlay=AemPriceOverlay(client=httpx.AsyncClient(transport=httpx.MockTransport(aem))),
    )


async def test_search_results_carry_the_points_price(
    overlaid: ClubMagentoCatalog, session: ShoppingSessionContext
) -> None:
    products = await overlaid.search_products(session, "headphone")
    headphone = products[0]
    assert headphone.price == 5000.0  # the overlay's CP price leads
    assert headphone.currency == "CP"
    assert headphone.attributes["cash_price_hkd"] == "2299"
    assert headphone.attributes["earn_clubpoints"] == "300"
    assert headphone.attributes["vendor"] == "digilife"


async def test_points_budget_search_answers_from_the_overlay(
    overlaid: ClubMagentoCatalog, requests: list[httpx.Request], session: ShoppingSessionContext
) -> None:
    results = await overlaid.search_products(
        session,
        "rewards",  # the distilled term, per the config's search notes
        SearchFilters(attributes={"clubpoints_max": "5000"}),
        limit=4,
    )
    assert [p.product_id for p in results] == ["NothingHP1", "CR-WEL-100-26B5"]  # dearest first
    assert all(p.currency == "CP" for p in results)
    assert requests == []  # the tiles price in CP; Magento was never asked


async def test_points_budget_search_narrows_by_text(
    overlaid: ClubMagentoCatalog, session: ShoppingSessionContext
) -> None:
    results = await overlaid.search_products(
        session,
        "voucher",
        SearchFilters(attributes={"clubpoints_max": "5000"}),
    )
    assert [p.product_id for p in results] == ["CR-WEL-100-26B5"]


async def test_budget_without_overlay_still_searches_text(
    backend: ClubMagentoCatalog, requests: list[httpx.Request], session: ShoppingSessionContext
) -> None:
    results = await backend.search_products(
        session, "keyboard", SearchFilters(attributes={"clubpoints_max": "5000"})
    )
    assert len(results) == 3  # fell back to Magento text search, as before


async def test_demo_member_context() -> None:
    def graphql(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=EMPTY_RESPONSE)

    session = ShoppingSessionContext(session_id="s", user_id="u")
    catalog = ClubMagentoCatalog(
        client=httpx.AsyncClient(transport=httpx.MockTransport(graphql)),
        demo_tier="gold",
        demo_clubpoints=10000,
    )
    context = await catalog.get_account_context(session)
    assert context == {
        "member": "demo stand-in until the member token lands",
        "tier": "gold",
        "clubpoints_balance": "10,000",
    }
    assert (await catalog.get_preferences(session)).loyalty_tier == "gold"


# -- The Phase 3 cart: a stateful fake of the shop's guest-cart GraphQL --------------------

from shopping_agent import Cart, CartItem  # noqa: E402


class FakeClubShop:
    """The guest-quote behavior the live endpoint showed: creates on demand, sells a
    configurable's variant under its parent, prices a redemption at 0 for guests, and
    takes update/remove as an input object keyed by the base64-decoded item id."""

    def __init__(self) -> None:
        self.cart_id: str | None = None
        self.lines: list[dict] = []  # {uid, item_id, sku, quantity, price}
        self.added: list[dict] = []
        self.price_of = {"4188051": 368.0, "NothingHP1": 1799.0, "CR-WEL-100-26B5": 0.0}

    @staticmethod
    def _uid(item_id: int) -> str:
        import base64

        return base64.b64encode(str(item_id).encode()).decode()

    def payload(self, request: httpx.Request) -> dict:
        body = json.loads(request.content)
        name, variables = body["operationName"], body.get("variables", {})
        if name == "CreateEmptyCart":
            self.cart_id = "guest-cart-1"
            return {"data": {"createEmptyCart": self.cart_id}}
        if name == "SearchProducts":
            return SEARCH_RESPONSE
        if name == "ProductDetail":
            return DETAIL_RESPONSE
        if name == "AddProductsToCart":
            self.added.append(variables["items"][0])
            for item in variables["items"]:
                if item["sku"] not in self.price_of:
                    return errors([f"{item['sku']}: not found"])
                # The live shop labels a configurable line by its family, hiding the child.
                self.lines.append(
                    {
                        "uid": self._uid(238796300 + len(self.lines)),
                        "item_id": 238796300 + len(self.lines),
                        "sku": item.get("parent_sku") or item["sku"],
                        "quantity": item["quantity"],
                        "price": self.price_of[item["sku"]],
                    }
                )
            return {
                "data": {"addProductsToCart": {"user_errors": [], "cart": {"total_quantity": 1}}}
            }
        if name == "UpdateCartItems":
            for update in variables["input"]["cart_items"]:
                for line in self.lines:
                    if line["item_id"] == update["cart_item_id"]:
                        line["quantity"] = update["quantity"]
            return {"data": {"updateCartItems": {"cart": {"total_quantity": 1}}}}
        if name == "RemoveItemFromCart":
            removed = variables["input"]["cart_item_id"]
            self.lines = [line for line in self.lines if line["item_id"] != removed]
            return {"data": {"removeItemFromCart": {"cart": {"total_quantity": 0}}}}
        if name == "Cart":
            return {
                "data": {
                    "cart": {
                        "total_quantity": len(self.lines),
                        "prices": {"grand_total": {"value": 0, "currency": "HKD"}},
                        "items": [
                            {
                                "uid": line["uid"],
                                "product": {"sku": line["sku"], "name": f"Item {line['sku']}"},
                                "quantity": line["quantity"],
                                "prices": {
                                    "price": {
                                        "value": line["price"],
                                        "currency": "HKD",
                                    },
                                    "row_total": {
                                        "value": line["price"] * line["quantity"],
                                        "currency": "HKD",
                                    },
                                },
                            }
                            for line in self.lines
                        ],
                    }
                }
            }
        raise AssertionError(f"unexpected operation {name}")

    def errors(self, messages: list[str]) -> dict:
        return {
            "data": {
                "addProductsToCart": {
                    "user_errors": [{"code": "UNDEFINED", "message": m} for m in messages],
                    "cart": {"total_quantity": 0},
                }
            }
        }


def errors(messages):  # noqa: E306 - small helper the payload builder closes over
    return {
        "data": {
            "addProductsToCart": {
                "user_errors": [{"code": "UNDEFINED", "message": m} for m in messages],
                "cart": {"total_quantity": 0},
            }
        }
    }


@pytest.fixture
def shop() -> FakeClubShop:
    return FakeClubShop()


@pytest.fixture
def store(shop: FakeClubShop) -> ClubMagentoCatalog:
    return ClubMagentoCatalog(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=shop.payload(request))
            )
        )
    )


async def test_fresh_cart_is_empty_without_a_request(
    store: ClubMagentoCatalog, shop: FakeClubShop, session: ShoppingSessionContext
) -> None:
    cart = await store.get_cart(session)
    assert cart.items == []
    assert shop.cart_id is None  # no guest quote is created until something is added


async def test_add_variant_under_its_family(
    store: ClubMagentoCatalog, shop: FakeClubShop, session: ShoppingSessionContext
) -> None:
    assert await store.get_product_details(session, "Clicks_16PM")
    cart = await store.add_to_cart(session, "4188051", 2)
    assert shop.added == [{"sku": "4188051", "quantity": 2, "parent_sku": "Clicks_16PM"}]
    assert [(i.product_id, i.quantity, i.price) for i in cart.items] == [("4188051", 2, 368.0)]


async def test_redemption_refused_for_guests(
    store: ClubMagentoCatalog, shop: FakeClubShop, session: ShoppingSessionContext
) -> None:
    with pytest.raises(Unavailable) as refused:
        await store.add_to_cart(session, "CR-WEL-100-26B5", 1)
    assert "member-only" in str(refused.value)
    assert shop.cart_id is None  # refused before any quote existed


async def test_user_errors_become_unavailable(
    store: ClubMagentoCatalog, session: ShoppingSessionContext
) -> None:
    with pytest.raises(Unavailable, match="not found"):
        await store.add_to_cart(session, "GONE-SKU", 1)


async def test_update_and_remove(
    store: ClubMagentoCatalog, shop: FakeClubShop, session: ShoppingSessionContext
) -> None:
    await store.add_to_cart(session, "4188051", 2)
    cart = await store.update_cart_item(session, "4188051", 1)
    assert [(i.product_id, i.quantity) for i in cart.items] == [("4188051", 1)]
    cart = await store.remove_from_cart(session, "4188051")
    assert cart.items == []
    assert shop.lines == []


async def test_update_of_absent_line_leaves_cart(
    store: ClubMagentoCatalog, shop: FakeClubShop, session: ShoppingSessionContext
) -> None:
    await store.add_to_cart(session, "NothingHP1", 1)
    cart = await store.update_cart_item(session, "NOT-IN-CART", 3)
    assert len(cart.items) == 1 and cart.items[0].quantity == 1


async def test_checkout_hands_off_to_the_site(
    store: ClubMagentoCatalog, session: ShoppingSessionContext
) -> None:
    assert await store.get_product_details(session, "Clicks_16PM")  # records the page url
    cart = Cart(
        items=[
            CartItem(product_id="4188051", title="Clicks keyboard (Spice)", price=368.0, quantity=1)
        ]
    )
    handoffs = await store.checkout_handoff(session, cart)
    assert handoffs[0].url == "https://shop.theclub.com.hk/checkout/cart/"
    urls = [handoff.url for handoff in handoffs[1:]]
    assert "https://shop.theclub.com.hk/clicks-keyboard-iphone-16-pro-max" in urls
    assert all(handoff.url.startswith("https://shop.theclub.com.hk/") for handoff in handoffs)


async def test_checkout_handoff_empty_cart(
    store: ClubMagentoCatalog, session: ShoppingSessionContext
) -> None:
    assert await store.checkout_handoff(session, Cart(currency="HKD")) == []
