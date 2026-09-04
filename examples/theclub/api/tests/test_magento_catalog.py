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

from shopping_agent import SearchFilters, ShoppingSessionContext

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
        await backend.get_cart(session)
    with pytest.raises(RuntimeError):
        await backend.add_to_cart(session, "NothingHP1", 1)
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
