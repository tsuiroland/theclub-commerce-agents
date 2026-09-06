# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""HktvMallCatalog against a captured shape of the Algolia hktvProduct hit (offline,
via httpx.MockTransport): field mapping, the numeric price filter, direct sku lookup,
and the honest unavailable answer every unwired system gives."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from shopping_agent import SearchFilters, ShoppingSessionContext

from ..hktv_catalog import DEFAULT_INDEX, HktvMallCatalog

# The live index's hit shape, trimmed to the fields this backend reads — the
# composite ``code`` and the alternate-code array as captured from the espresso
# probe (other values stand in for their captured shape).
HIT: dict[str, Any] = {
    "code": "H5568003_S_Final_UX1000_Greige",
    "productSearchCode": "F10003",
    "allProductSearchCode": ["F10003", "HKTV-F10003"],
    "objectID": "9126792187430",
    "baseProduct": "H5568003Final_UX1000",
    "nameEn": "Final UX1000 Hybird ANC Wireless Bluetooth Headphones｜Greige",
    "nameZh": "Final UX1000 混合式主動降噪無線藍牙頭戴耳機｜米灰色",
    "brand": "Final",
    "brandEn": "final",
    "sellingPrice": 488.0,
    "priceList": [
        {
            "currencyIso": "HKD",
            "value": 499.0,
            "priceType": "BUY",
            "formattedValue": "$ 499.00",
        }
    ],
    "averageRating": 4.6,
    "numberOfReviews": 12,
    "numberOfColors": 3,
    "images": [
        {
            "imageType": "PRIMARY",
            "format": "product",
            "url": "https://cdn-media.hktvmall.com/hktv-mms/HKTV/product/H5568003.jpg",
        }
    ],
    "mainCatNameEn": ["Gadgets & Electronics"],
    "subCat2NameEn": ["Bluetooth Earphone", "Headphone"],
    "summaryEn": "Hybrid ANC wireless headphones with 80-hour playback.",
    "promotionTextEn": "",
    "countryOfOriginEn": "China",
    "packingSpecEn": "1 piece",
    "deliveryTime": "2-3 days",
    "loyaltyPoint": 244,
    "hasStock": True,
    "storeNameEn": "Digi Life",
    "urlEn": "main/Digi-Life/s/H5568003/Gadgets-%26-Electronics/Headphone-%26-Earphone",
    "stock": {"stockLevelStatus": {"code": "inStock"}},
}

SESSION = ShoppingSessionContext(session_id="s-1", user_id="u-1")


def algolia(hit_lists: list[list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], HktvMallCatalog]:
    """A transport answering each POST with the next scripted hit list, recording the
    requests the backend sent."""
    calls: list[dict[str, Any]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "body": json.loads(request.content)})
        return httpx.Response(200, json={"results": [{"hits": hit_lists.pop(0)}]})

    return calls, HktvMallCatalog(
        client=httpx.AsyncClient(transport=httpx.MockTransport(transport))
    )


def sent_params(call: dict[str, Any]) -> dict[str, list[str]]:
    return parse_qs(call["body"]["requests"][0]["params"])


async def test_search_maps_the_hit() -> None:
    hidden = {**HIT, "code": "H9999999", "invisible": True}
    calls, catalog = algolia([[HIT, hidden]])
    products = await catalog.search_products(SESSION, "headphone", limit=6)

    assert len(products) == 1  # the invisible sibling is not a result
    product = products[0]
    assert product.product_id == "H5568003_S_Final_UX1000_Greige"
    assert product.title.startswith("Final UX1000")
    assert product.price == 488.0
    assert product.currency == "HKD"
    assert product.brand == "final"
    assert product.category == "Gadgets & Electronics"
    assert product.in_stock is True
    assert product.rating == 4.6
    assert product.review_count == 12
    assert product.image_url is not None and product.image_url.startswith("https://cdn-media.")
    assert product.attributes["store"] == "HKTV Mall"
    assert product.attributes["merchant"] == "Digi Life"
    assert product.attributes["url"].startswith("https://www.hktvmall.com/hktv/en/main/Digi-Life")
    assert product.attributes["loyalty_point"] == "244"
    assert product.attributes["search_code"] == "F10003"

    request = calls[0]["body"]["requests"][0]
    assert calls[0]["url"].endswith("/1/indexes/*/queries")
    assert request["indexName"] == DEFAULT_INDEX
    assert sent_params(calls[0])["query"] == ["headphone"]
    assert sent_params(calls[0])["hitsPerPage"] == ["6"]


async def test_price_bounds_map_to_numeric_filters() -> None:
    calls, catalog = algolia([[HIT]])
    await catalog.search_products(
        SESSION, "headphone", SearchFilters(min_price=100.0, max_price=500.0)
    )
    assert sent_params(calls[0])["numericFilters"] == ["sellingPrice>=100,sellingPrice<=500"]


async def test_buy_price_falls_back_to_the_price_list() -> None:
    no_selling = {key: value for key, value in HIT.items() if key != "sellingPrice"}
    _, catalog = algolia([[no_selling]])
    products = await catalog.search_products(SESSION, "headphone")
    assert products[0].price == 499.0


async def test_detail_resolves_the_code_with_specs() -> None:
    calls, catalog = algolia([[HIT]])
    details = await catalog.get_product_details(SESSION, HIT["code"])

    assert details is not None
    assert details.product_id == "H5568003_S_Final_UX1000_Greige"
    assert details.short_description is not None
    assert details.specs["brand"] == "final"
    assert details.specs["colours"] == "3"
    assert details.specs["country_of_origin"] == "China"
    assert sent_params(calls[0])["filters"] == ['code:"H5568003_S_Final_UX1000_Greige"']


async def test_unknown_or_mismatched_code_is_none() -> None:
    near_miss = {**HIT, "code": "H5568003_S_Final_UX1000_Black"}
    calls, catalog = algolia([[], [near_miss]])
    assert await catalog.get_product_details(SESSION, HIT["code"]) is None
    assert await catalog.get_product_details(SESSION, HIT["code"]) is None


async def test_unwired_systems_answer_unavailable() -> None:
    _, catalog = algolia([[]])
    with pytest.raises(RuntimeError, match="not wired yet"):
        await catalog.add_to_cart(SESSION, HIT["code"], 1)
    with pytest.raises(RuntimeError, match="not wired yet"):
        await catalog.get_orders(SESSION)
    with pytest.raises(RuntimeError, match="not wired yet"):
        await catalog.search_policies(SESSION, "returns")
    with pytest.raises(RuntimeError, match="not wired yet"):
        await catalog.get_fulfillment_options(SESSION, [HIT["code"]])


async def test_guest_reads_are_an_empty_cart_and_plain_preferences() -> None:
    _, catalog = algolia([[]])
    cart = await catalog.get_cart(SESSION)
    assert cart.currency == "HKD" and cart.items == []
    preferences = await catalog.get_preferences(SESSION)
    assert preferences.user_id == "u-1"
