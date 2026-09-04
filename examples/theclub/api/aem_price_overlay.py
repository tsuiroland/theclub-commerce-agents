# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The Club's Clubpoints pricing as an anonymous overlay on the Magento catalog.

Magento's anonymous GraphQL serves HKD cash prices only — the Clubpoints redemption
price is member-gated there — but the AEM shopping pages (theclub.com.hk) publish it
anonymously in their page models, one JSON document per merchandised page, each tile
carrying the Magento ``sku`` alongside ``priceDisplay`` dual pricing, the vendor, the
rewards/shopping split, and The Club's own category tree. This module loads a
configured set of those models (lazily, cached, per-page TTL), indexes the tiles by
sku, and lets the catalog state the points price of a product it found in Magento — or
answer a points-budget search outright from the tiles, which carry every identity
field a result needs.

The tiles are AEM's cache of pages its editors merchandised, not a query API: coverage
is those pages, freshness is their cache, and the member-gated Magento price (Phase 2's
second half, with the member token) remains the source of truth once a session carries
one. A page that fails to load is logged and skipped; the catalog keeps its Magento
data and says nothing it cannot ground."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from logging import Logger, getLogger
from typing import Any

import httpx

from shopping_agent import Product

DEFAULT_MODEL_URLS = ("https://www.theclub.com.hk/shopping/en/lc/clubpoints-zone.model.json",)

_logger: Logger = getLogger("theclub.aem_overlay")

# Words that name the redemption itself; a budget query full of them ("what can I
# redeem with my clubpoints") is asking for the whole budget range, not a product.
_POINTS_VOCABULARY = frozenset(
    {
        "cp",
        "clubpoint",
        "clubpoints",
        "point",
        "points",
        "redeem",
        "redemption",
        "reward",
        "rewards",
    }
)


def _amount(value: Any) -> float | None:
    """AEM prices are strings with thousands separators; ``None`` when absent."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


@dataclass(frozen=True)
class Tile:
    """One AEM product tile, the fields the catalog states."""

    sku: str
    name: str
    brand: str | None = None
    url: str | None = None  # the product page on shop.theclub.com.hk
    image: str | None = None
    in_stock: bool = True
    clubpoints: float | None = None  # finalPrice.cp / minClubPoints
    cash_price: float | None = None  # finalPrice.hkd, crossedPrice.hkd, originalPrice
    earn_clubpoints: int = 0  # extraClubPoints on top of the base earn
    rewards_or_shopping: str | None = None  # The Club's own classification
    vendor: str | None = None
    category: str | None = None  # subCat1 of The Club's tree
    product_type: str | None = None

    def as_product(self) -> Product:
        """A whole result from the tile alone — what a budget search returns."""
        attributes = {
            key: value
            for key, value in (
                (
                    "cash_price_hkd",
                    _trim(self.cash_price) if self.cash_points_priced and self.cash_price else None,
                ),
                (
                    "clubpoints",
                    _trim(self.clubpoints)
                    if not self.cash_points_priced and self.clubpoints
                    else None,
                ),
                ("vendor", self.vendor),
                ("url", self.url),
                ("catalog", self.rewards_or_shopping),
                ("product_type", self.product_type),
                ("earn_clubpoints", str(self.earn_clubpoints) if self.earn_clubpoints else None),
            )
            if value is not None and value != ""
        }
        return Product(
            product_id=self.sku,
            title=self.name,
            brand=self.brand,
            price=self.clubpoints if self.cash_points_priced else (self.cash_price or 0.0),
            currency="CP" if self.cash_points_priced else "HKD",
            image_url=self.image,
            category=self.category,
            in_stock=self.in_stock,
            attributes=attributes,
        )

    @property
    def cash_points_priced(self) -> bool:
        """Points-priced items lead with CP; cash items keep their HKD price."""
        return self.clubpoints is not None and self.clubpoints > 0

    def merged_attributes(self, current: dict[str, str]) -> dict[str, str]:
        """What a Magento-sourced product should add or correct, keeping the model's
        existing attributes unless the tile states better."""
        attributes = dict(current)
        if self.cash_points_priced:
            attributes.pop("price_note", None)  # the points price is no longer gated
            if self.cash_price:
                attributes["cash_price_hkd"] = _trim(self.cash_price)
        if self.rewards_or_shopping:
            attributes["catalog"] = self.rewards_or_shopping.lower()
        if self.vendor:
            attributes["vendor"] = self.vendor
        if self.url:
            attributes["url"] = self.url
        if self.earn_clubpoints:
            attributes["earn_clubpoints"] = str(self.earn_clubpoints)
        return attributes


def _trim(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _tile(node: dict[str, Any]) -> Tile | None:
    sku = node.get("sku")
    if not sku or "priceDisplay" not in node:
        return None
    display = node["priceDisplay"]
    final = display.get("finalPrice") or {}
    crossed = display.get("crossedPrice") or {}
    return Tile(
        sku=str(sku),
        name=str(node.get("name") or sku),
        brand=node.get("brandLabel"),
        url=node.get("url"),
        image=node.get("cover") or node.get("thumbnail"),
        in_stock=bool(node.get("inStock", node.get("stockStatus") == "IN_STOCK")),
        clubpoints=_amount(final.get("cp")) or _amount(node.get("minClubPoints")),
        cash_price=_amount(final.get("hkd"))
        or _amount(crossed.get("hkd"))
        or _amount(node.get("originalPrice")),
        earn_clubpoints=int(node.get("extraClubPoints") or 0),
        rewards_or_shopping=node.get("rewardsOrShopping"),
        vendor=node.get("vendorName"),
        category=node.get("subCat1") or node.get("categoryName"),
        product_type=node.get("productType"),
    )


def _walk_tiles(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, dict):
        if node.get("sku") and "priceDisplay" in node:
            yield node
        for value in node.values():
            yield from _walk_tiles(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_tiles(value)


@dataclass
class _Page:
    url: str
    loaded_at: float | None = None
    tiles: dict[str, Tile] = field(default_factory=dict)


class AemPriceOverlay:
    """The sku-indexed tiles of the configured AEM pages, refreshed behind a TTL."""

    def __init__(
        self,
        model_urls: Sequence[str] = DEFAULT_MODEL_URLS,
        client: httpx.AsyncClient | None = None,
        ttl_seconds: float = 600.0,
        clock=time.monotonic,
    ) -> None:
        self._pages = [_Page(url) for url in model_urls]
        self._client = client or httpx.AsyncClient(
            timeout=20.0, headers={"User-Agent": "theclub-commerce-agent/0.1"}
        )
        self._ttl = ttl_seconds
        self._clock = clock

    async def _refresh(self, page: _Page) -> None:
        age = None if page.loaded_at is None else self._clock() - page.loaded_at
        if age is not None and age < self._ttl:
            return
        try:
            response = await self._client.get(page.url)
            response.raise_for_status()
            document = response.json()
        except (httpx.HTTPError, ValueError) as error:  # JSONDecodeError is a ValueError
            _logger.warning("AEM price page skipped (%s): %s", page.url, error)
            page.loaded_at = self._clock()  # retry only after the TTL, not every call
            return
        page.tiles = {}
        for node in _walk_tiles(document):
            if tile := _tile(node):
                page.tiles[tile.sku] = tile
        page.loaded_at = self._clock()
        points = sum(1 for tile in page.tiles.values() if tile.cash_points_priced)
        _logger.info(
            "theclub <- AEM %s (%d tiles, %d points-priced)",
            page.url,
            len(page.tiles),
            points,
        )

    async def tiles(self) -> list[Tile]:
        """Every tile of every page, refreshed as their TTLs lapse."""
        await asyncio.gather(*(self._refresh(page) for page in self._pages))
        tiles: dict[str, Tile] = {}
        for page in self._pages:
            tiles.update(page.tiles)
        return list(tiles.values())

    async def lookup(self, sku: str) -> Tile | None:
        for tile in await self.tiles():
            if tile.sku == sku:
                return tile
        return None

    async def enrich(self, product: Product) -> Product:
        """Fold the tile's pricing and merchandising into a Magento-sourced product."""
        if not (tile := await self.lookup(product.product_id)):
            return product
        data = product.model_dump()
        if tile.cash_points_priced:
            data["price"] = tile.clubpoints
            data["currency"] = "CP"
        elif tile.cash_price:
            data["price"] = tile.cash_price
            data["currency"] = "HKD"
        data["brand"] = tile.brand or product.brand
        data["image_url"] = tile.image or product.image_url
        data["attributes"] = tile.merged_attributes(product.attributes)
        return Product(**data)

    async def budget_search(
        self,
        maximum: float | None = None,
        minimum: float | None = None,
        text: str | None = None,
        limit: int = 8,
    ) -> list[Product]:
        """Points-priced tiles a member's budget reaches, dearest first, optionally
        narrowed to words matching the name, brand, category, or type. Points
        vocabulary ("clubpoints", "redeem", …) narrows nothing — every tile here is a
        redemption — so it is dropped rather than matched against names that never
        carry it."""
        words = [
            word
            for word in (text or "").lower().split()
            if len(word) > 1 and word not in _POINTS_VOCABULARY
        ]
        matches = []
        for tile in await self.tiles():
            if not tile.cash_points_priced or not tile.in_stock:
                continue
            if maximum is not None and tile.clubpoints > maximum:
                continue
            if minimum is not None and tile.clubpoints < minimum:
                continue
            haystack = " ".join(
                part for part in (tile.name, tile.brand, tile.category, tile.product_type) if part
            ).lower()
            if words and not any(word in haystack for word in words):
                continue
            matches.append(tile)
        matches.sort(key=lambda tile: -(tile.clubpoints or 0.0))
        return [tile.as_product() for tile in matches[:limit]]
