# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The live catalog dressed as the console's storefront, so the grid, the cart
drawer, and the add button read The Club instead of ACME's fixtures.

`LiveClubStorefront` is `ClubMagentoCatalog` plus the `DemoStorefront` surface the
shared host routes use: a products feed seeded from the AEM tiles (each tile carries
every field a grid card needs — image, dual price, url), a lookup the console's
detail panel serves synchronously from that seed, and a session reset that drops the
guest quote binding. The chat keeps the full catalog; the grid is the tiles'
merchandised order, dearest first, cash items from a Magento search alongside them."""

from __future__ import annotations

import logging

from shopping_agent import Order, ProductDetails, ShoppingSessionContext

from .magento_catalog import ClubMagentoCatalog

_logger = logging.getLogger("theclub.storefront")

FEED_SIZE = 24
CASH_FEED_QUERY = "mobile gadget"  # a breadth-y shopping term for the cash rows
_feed_session = ShoppingSessionContext(session_id="console-feed", user_id="console")


class LiveClubStorefront(ClubMagentoCatalog):
    """`ClubMagentoCatalog` serving the console itself."""

    store_name = "The Club"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.products: dict[str, ProductDetails] = {}

    async def load_feed(self) -> None:
        """Seed the grid: the points-priced tiles the overlay carries, plus a page of
        cash results; called at app startup, safe to call again to refresh."""
        seeded: dict[str, ProductDetails] = {}
        if self._overlay is not None:
            for tile in await self._overlay.budget_search(maximum=10**9, limit=FEED_SIZE):
                seeded[tile.product_id] = ProductDetails(**tile.model_dump())
        try:
            for product in await self.search_products(_feed_session, CASH_FEED_QUERY, limit=8):
                if product.has_options:
                    continue  # the grid's add button cannot choose a family's options
                seeded.setdefault(product.product_id, ProductDetails(**product.model_dump()))
        except RuntimeError as error:  # the shop shrugged; the tiles still seed the grid
            _logger.warning("theclub: cash feed skipped: %s", str(error)[:120])
        self.products = seeded
        self._remember(list(seeded.values()))
        _logger.info("theclub: console feed loaded (%d products)", len(seeded))

    def product(self, product_id: str) -> ProductDetails | None:
        return self.products.get(product_id)

    def reset_session(self, session_id: str) -> None:
        self._carts.pop(session_id, None)  # the guest quote goes; a member cart is theirs

    def recent_orders(self, limit: int = 6) -> list[Order]:
        return []  # the merchant overview's cross-user feed; The Club runs no portal here
