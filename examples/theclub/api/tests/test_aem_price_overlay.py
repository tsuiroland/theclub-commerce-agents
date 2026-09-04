# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""AemPriceOverlay against a trimmed shape of theclub.com.hk's shopping page models
(offline, via httpx.MockTransport): parsing, sku lookup, product enrichment, the
points-budget search, and degradation when a page fails."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from shopping_agent import Product

from ..aem_price_overlay import AemPriceOverlay

# Trimmed from /shopping/en/lc/clubpoints-zone.model.json: tiles nested the way AEM
# nests them, one points-priced voucher, one cash product, one out of stock.
MODEL: dict[str, Any] = {
    ":type": "theclub-shopping-spa-react/components/page",
    ":items": {
        "root": {
            ":items": {
                "responsivegrid": {
                    ":items": {
                        "tierbannerproductlist": {
                            ":items": {
                                "product_1": {
                                    "id": 320784,
                                    "sku": "CR-WEL-100-26B5",
                                    "name": "Wellcome - HK$100 E-Shopping Voucher",
                                    "brandLabel": "Wellcome",
                                    "stockStatus": "IN_STOCK",
                                    "inStock": True,
                                    "priceDisplay": {
                                        "crossedPrice": {"cp": None, "hkd": "100.0"},
                                        "finalPrice": {"cp": "690", "hkd": None},
                                    },
                                    "minClubPoints": 690,
                                    "originalPrice": 100.0,
                                    "extraClubPoints": 0,
                                    "subCat1": "Rewards",
                                    "rewardsOrShopping": "Rewards",
                                    "productType": "virtual",
                                    "vendorId": 1273,
                                    "vendorName": "Wellcome",
                                    "cover": "https://shop.theclub.com.hk/m/wel.jpg",
                                },
                                "product_2": {
                                    "sku": "Newage_M49",
                                    "name": "Newage True Wireless Earbuds M49",
                                    "brandLabel": "Newage",
                                    "inStock": True,
                                    "priceDisplay": {
                                        "crossedPrice": {"cp": None, "hkd": "349.0"},
                                        "finalPrice": {"cp": None, "hkd": "299.0"},
                                    },
                                    "minClubPoints": None,
                                    "originalPrice": 349.0,
                                    "extraClubPoints": 150,
                                    "subCat1": "Mobile PC & Gadgets",
                                    "rewardsOrShopping": "Shopping",
                                    "vendorName": "digilife",
                                },
                                "product_3": {
                                    "sku": "CR-EXP-500",
                                    "name": "Expired Redemption",
                                    "inStock": False,
                                    "priceDisplay": {
                                        "finalPrice": {"cp": "400", "hkd": None},
                                    },
                                    "minClubPoints": 400,
                                    "rewardsOrShopping": "Rewards",
                                },
                            }
                        }
                    }
                }
            }
        }
    },
}


@pytest.fixture
def fetches() -> list[int]:
    return [0]


@pytest.fixture
def overlay(fetches: list[int]) -> AemPriceOverlay:
    def handler(request: httpx.Request) -> httpx.Response:
        fetches[0] += 1
        return httpx.Response(200, json=MODEL)

    return AemPriceOverlay(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        clock=lambda: 0.0,  # within the TTL on every read: one fetch, then cached
    )


async def test_tiles_parsed_and_indexed(overlay: AemPriceOverlay) -> None:
    tile = await overlay.lookup("CR-WEL-100-26B5")
    assert tile is not None
    assert tile.clubpoints == 690.0
    assert tile.cash_price == 100.0
    assert tile.vendor == "Wellcome"
    assert tile.category == "Rewards"
    assert tile.rewards_or_shopping == "Rewards"


async def test_enrich_points_priced_product_leads_with_cp(overlay: AemPriceOverlay) -> None:
    # What Magento serves anonymously for a redemption: HKD 0 plus a price note.
    gated = Product(
        product_id="CR-WEL-100-26B5",
        title="Wellcome - HK$100 E-Shopping Voucher",
        price=0.0,
        currency="HKD",
        attributes={"price_note": "Clubpoints redemption item: login for the price"},
    )
    enriched = await overlay.enrich(gated)
    assert enriched.price == 690.0
    assert enriched.currency == "CP"
    assert enriched.attributes["cash_price_hkd"] == "100"
    assert enriched.attributes["catalog"] == "rewards"
    assert "price_note" not in enriched.attributes


async def test_enrich_cash_product_keeps_hkd_and_adds_earn(overlay: AemPriceOverlay) -> None:
    cash = Product(product_id="Newage_M49", title="Newage True Wireless Earbuds M49", price=349.0)
    enriched = await overlay.enrich(cash)
    assert enriched.price == 299.0  # AEM's final cash price over Magento's regular
    assert enriched.currency == "HKD"
    assert enriched.attributes["earn_clubpoints"] == "150"
    assert enriched.attributes["vendor"] == "digilife"


async def test_enrich_unknown_sku_passes_through(overlay: AemPriceOverlay) -> None:
    product = Product(product_id="UNKNOWN", title="Unlisted", price=10.0)
    assert await overlay.enrich(product) == product


async def test_budget_search_spends_to_the_cap(overlay: AemPriceOverlay) -> None:
    results = await overlay.budget_search(maximum=5000, limit=8)
    assert [p.product_id for p in results] == ["CR-WEL-100-26B5"]  # out-of-stock skipped
    assert results[0].price == 690.0
    assert results[0].currency == "CP"


async def test_budget_search_narrows_by_words(overlay: AemPriceOverlay) -> None:
    results = await overlay.budget_search(maximum=100000, text="wellcome voucher")
    assert [p.product_id for p in results] == ["CR-WEL-100-26B5"]
    assert await overlay.budget_search(maximum=5000, text="iphone") == []


async def test_budget_search_bounds(overlay: AemPriceOverlay) -> None:
    assert await overlay.budget_search(maximum=500) == []
    assert await overlay.budget_search(minimum=500) != []


async def test_points_vocabulary_narrows_nothing(overlay: AemPriceOverlay) -> None:
    # "what can I redeem with my clubpoints" is a budget question, not a product name.
    results = await overlay.budget_search(maximum=5000, text="clubpoints redemption")
    assert [p.product_id for p in results] == ["CR-WEL-100-26B5"]


async def test_failed_page_degrades_to_no_tiles() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    overlay = AemPriceOverlay(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await overlay.tiles() == []
    assert await overlay.budget_search(maximum=10000) == []


async def test_pages_cached_behind_the_ttl(fetches: list[int]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        fetches[0] += 1
        return httpx.Response(200, json=MODEL)

    ticks = [0.0]
    overlay = AemPriceOverlay(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        clock=lambda: ticks[0],
    )
    await overlay.tiles()
    await overlay.tiles()
    assert fetches[0] == 1  # within the TTL: served from cache
    ticks[0] = 601.0
    await overlay.tiles()
    assert fetches[0] == 2  # past it: fetched again


def test_fixture_walker_finds_nested_tiles() -> None:
    # The walker must reach tiles however deep AEM nests them; the fixture keeps one
    # real nesting level and this pins it at three.
    from ..aem_price_overlay import _walk_tiles

    assert len(list(_walk_tiles(json.loads(json.dumps(MODEL))))) == 3
