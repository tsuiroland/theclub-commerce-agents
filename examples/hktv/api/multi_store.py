# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""One storefront over several stores: the federated backend a multi-store
deployment hands the agent. Search fans out to every store and merges the lanes
round-robin so stores alternate; every product id is namespaced ``store:sku`` so
one provenance record spans stores, and each result's ``attributes.store`` names
where it came from. Cart lines keep their store: a write routes by its id's
prefix and the merged cart shows each store's lines under namespaced ids, while
``checkout`` hands off per store (no combined checkout exists in the world). The
first store in the mapping is the primary: member context, orders, policies, and
fulfillment read from it, because those surfaces belong to a signed-in account
that only the primary carries so far. A store that fails a fan-out contributes
nothing and logs a warning — the other stores' results still answer."""

from __future__ import annotations

import asyncio
import logging
from typing import TypeVar

from shopping_agent import (
    Cart,
    CheckoutHandoff,
    NotOffered,
    Order,
    Product,
    ProductDetails,
    SearchFilters,
    ShoppingSessionContext,
    StorefrontBackend,
    UserPreferences,
)

_logger = logging.getLogger("hktv.multi")

ProductT = TypeVar("ProductT", bound=Product)

_UNROUTED = (
    "{product_id} names no store: use the id exactly as the tool returned it "
    "(for example hktv:CODE or theclub:SKU)"
)


class MultiStoreBackend(StorefrontBackend):
    """Wraps one backend per store, keyed by id prefix in display order; the first
    entry is the primary whose account surfaces the deployment reads."""

    store_name = "The Club × HKTV Mall"

    def __init__(self, stores: dict[str, StorefrontBackend]) -> None:
        if not stores:
            raise ValueError("a multi-store backend needs at least one store")
        self._stores = dict(stores)
        self._primary = next(iter(self._stores.values()))

    # -- Routing ------------------------------------------------------------------

    def _route(self, product_id: str) -> tuple[str, str, StorefrontBackend] | None:
        """``(prefix, raw id, backend)`` for a namespaced id, else None."""
        prefix, separator, raw = product_id.partition(":")
        if separator and raw and prefix in self._stores:
            return prefix, raw, self._stores[prefix]
        return None

    def _namespaced(self, prefix: str, product: ProductT) -> ProductT:
        data = product.model_dump()
        data["product_id"] = f"{prefix}:{product.product_id}"
        if product.variant_of:
            data["variant_of"] = f"{prefix}:{product.variant_of}"
        attributes = dict(data.get("attributes") or {})
        attributes["store"] = getattr(self._stores[prefix], "store_name", prefix)
        data["attributes"] = attributes
        rebuilt = type(product).model_validate(data)
        if isinstance(rebuilt, ProductDetails):
            rebuilt.variants = [self._namespaced(prefix, variant) for variant in rebuilt.variants]
        return rebuilt

    def _store_label(self, prefix: str) -> str:
        return getattr(self._stores[prefix], "store_name", prefix)

    # -- Catalog ------------------------------------------------------------------

    async def search_products(
        self,
        session: ShoppingSessionContext,
        query: str,
        filters: SearchFilters | None = None,
        limit: int = 8,
    ) -> list[Product]:
        outcomes = await asyncio.gather(
            *(
                store.search_products(session, query, filters, limit)
                for store in self._stores.values()
            ),
            return_exceptions=True,
        )
        lanes: list[list[Product]] = []
        for (prefix, _store), outcome in zip(self._stores.items(), outcomes, strict=True):
            if isinstance(outcome, BaseException):
                _logger.warning("stores: %s search unavailable: %s", prefix, str(outcome)[:120])
                continue
            lanes.append([self._namespaced(prefix, product) for product in outcome])
        merged: list[Product] = []
        for index in range(max((len(lane) for lane in lanes), default=0)):
            for lane in lanes:  # round-robin: the stores alternate in the answer
                if index < len(lane):
                    merged.append(lane[index])
        return merged[:limit]

    async def get_product_details(
        self, session: ShoppingSessionContext, product_id: str
    ) -> ProductDetails | None:
        routed = self._route(product_id)
        attempts = [routed] if routed else [(p, product_id, s) for p, s in self._stores.items()]
        for prefix, raw, store in attempts:
            try:
                details = await store.get_product_details(session, raw)
            except Exception as error:  # one store's outage is not a missed product
                _logger.warning("stores: %s details unavailable: %s", prefix, str(error)[:120])
                continue
            if details is not None:
                return self._namespaced(prefix, details)
        return None

    # -- Cart: each line keeps its store -------------------------------------------

    async def get_cart(self, session: ShoppingSessionContext) -> Cart:
        outcomes = await asyncio.gather(
            *(store.get_cart(session) for store in self._stores.values()),
            return_exceptions=True,
        )
        items = []
        currency = "HKD"
        for (prefix, _store), outcome in zip(self._stores.items(), outcomes, strict=True):
            if isinstance(outcome, BaseException):
                continue  # a store with no cart to read contributes no lines
            currency = outcome.currency
            for item in outcome.items:
                update = {"product_id": f"{prefix}:{item.product_id}"}
                if item.variant_of:
                    update["variant_of"] = f"{prefix}:{item.variant_of}"
                items.append(item.model_copy(update=update))
        return Cart(items=items, currency=currency)

    async def _routed_write(self, product_id: str) -> tuple[str, str, StorefrontBackend]:
        routed = self._route(product_id)
        if routed is None:
            raise NotOffered(_UNROUTED.format(product_id=product_id))
        return routed

    async def add_to_cart(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        _prefix, raw, store = await self._routed_write(product_id)
        await store.add_to_cart(session, raw, quantity)
        return await self.get_cart(session)

    async def update_cart_item(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        _prefix, raw, store = await self._routed_write(product_id)
        await store.update_cart_item(session, raw, quantity)
        return await self.get_cart(session)

    async def remove_from_cart(self, session: ShoppingSessionContext, product_id: str) -> Cart:
        _prefix, raw, store = await self._routed_write(product_id)
        await store.remove_from_cart(session, raw)
        return await self.get_cart(session)

    async def checkout_handoff(
        self, session: ShoppingSessionContext, cart: Cart
    ) -> list[CheckoutHandoff]:
        handoffs: list[CheckoutHandoff] = []
        for _prefix, store in self._stores.items():
            try:
                own = await store.get_cart(session)
            except Exception:
                continue  # a store with no cart cannot check out
            if own.items:
                handoffs.extend(await store.checkout_handoff(session, own))
        return handoffs

    # -- The primary store's account surfaces ---------------------------------------

    async def get_preferences(self, session: ShoppingSessionContext) -> UserPreferences:
        preferences = await self._primary.get_preferences(session)
        return preferences.model_copy(
            update={"preferences": preferences.preferences | {"stores": " ".join(self._stores)}}
        )

    async def get_account_context(self, session: ShoppingSessionContext):
        return await self._primary.get_account_context(session)

    async def get_orders(self, session: ShoppingSessionContext, limit: int = 5) -> list[Order]:
        return await self._primary.get_orders(session, limit)

    async def get_order(self, session: ShoppingSessionContext, order_id: str) -> Order | None:
        return await self._primary.get_order(session, order_id)

    async def search_policies(self, session: ShoppingSessionContext, query: str):
        return await self._primary.search_policies(session, query)

    async def get_fulfillment_options(
        self, session: ShoppingSessionContext, product_ids: list[str]
    ):
        per_store: dict[StorefrontBackend, list[str]] = {}
        for product_id in product_ids:
            if routed := self._route(product_id):
                per_store.setdefault(routed[2], []).append(routed[1])
        options = []
        for store, ids in per_store.items():
            try:
                options.extend(await store.get_fulfillment_options(session, ids))
            except Exception as error:
                _logger.warning("stores: fulfillment unavailable: %s", str(error)[:120])
        return options
