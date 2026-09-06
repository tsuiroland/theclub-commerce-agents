# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The multi-store example API — one assistant over The Club and HKTV Mall.

    uvicorn hktv.api.main:app --app-dir examples --reload --port 8005

The Club rides the same live backend and ``.env`` as the theclub vertical
(``CLUB_BACKEND=magento``, the member token, the local-LLM variables); HKTV Mall
rides its Algolia catalog (``hktv_catalog.py``). Search fans out to both, results
carry their store, and ids are namespaced ``theclub:``/``hktv:``. The console is
the Club storefront-web on :3005 serving the merged feed. Env resolution rides
along with the retail example's .env until this example gets its own data
directory."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from anthropic import AsyncAnthropic
from fastapi import FastAPI

from demo_common import REPO_ROOT, MemorySeeder, build_storefront_host, load_demo_env
from retail.api.mock_retail import DATA_DIR, MockRetail
from shopping_agent import Order, ProductDetails, ShoppingSessionContext, StorefrontBackend
from shopping_agent_runtime import ShoppingAgent
from theclub.api.aem_price_overlay import DEFAULT_MODEL_URLS, AemPriceOverlay
from theclub.api.live_storefront import LiveClubStorefront

from .agent_config import build_shopping_config
from .hktv_catalog import HktvMallCatalog
from .multi_store import MultiStoreBackend

load_demo_env(DATA_DIR.parent)

# Every fetch of the live Club surfaces prints a "theclub <-" line; HKTV Mall's
# Algolia reads print "hktv <-". Silence with CLUB_TRACE=0.
_trace = logging.getLogger("theclub")
if os.environ.get("CLUB_TRACE") != "0" and not _trace.handlers:
    _trace.addHandler(logging.StreamHandler())
    _trace.setLevel(logging.INFO)

live_mode = os.environ.get("CLUB_BACKEND") == "magento"

# The HKTV side of the console feed: a broad, reliable term for the grid's rows.
HKTV_FEED_QUERY = "headphone"
HKTV_FEED_SIZE = 12
_feed_session = ShoppingSessionContext(session_id="console-feed", user_id="console")


def club_storefront() -> StorefrontBackend:
    """The Club side, built exactly as the theclub vertical builds it."""
    if not live_mode:
        return MockRetail()
    models = tuple(
        url.strip()
        for url in os.environ.get("CLUB_AEM_MODELS", ",".join(DEFAULT_MODEL_URLS)).split(",")
        if url.strip()
    )
    balance = os.environ.get("CLUB_DEMO_CP", "")
    member = os.environ.get("CLUB_MEMBER_EMAIL")
    token = os.environ.get("CLUB_MEMBER_TOKEN")  # a harvested bearer, SSO-proof login
    return LiveClubStorefront(
        graphql_url=os.environ.get("CLUB_MAGENTO_URL", "https://shop.theclub.com.hk/graphql"),
        store_code=os.environ.get("CLUB_MAGENTO_STORE", "en_US"),
        overlay=AemPriceOverlay(model_urls=models),
        demo_tier=None if member or token else os.environ.get("CLUB_DEMO_TIER"),
        demo_clubpoints=None if member or token else (int(balance) if balance.isdigit() else None),
        email=member,
        password=os.environ.get("CLUB_MEMBER_PASSWORD"),
        token=token,
    )


class MultiStorefront(MultiStoreBackend):
    """The federated catalog serving the console itself: the Club feed (its tiles
    plus a page of cash results) and a page of HKTV Mall rows, one namespaced
    record per grid card."""

    def __init__(self, stores: dict[str, StorefrontBackend]) -> None:
        super().__init__(stores)
        self.products: dict[str, ProductDetails] = {}

    async def load_feed(self) -> None:
        club_rows: list[ProductDetails] = []
        club = self._stores.get("theclub")
        if isinstance(club, LiveClubStorefront):
            await club.load_feed()
            club_rows = [self._namespaced("theclub", d) for d in club.products.values()]
        hktv_rows: list[ProductDetails] = []
        try:
            for product in await self._stores["hktv"].search_products(
                _feed_session, HKTV_FEED_QUERY, limit=HKTV_FEED_SIZE
            ):
                hktv_rows.append(self._namespaced("hktv", ProductDetails(**product.model_dump())))
        except Exception as error:  # Algolia shrugged; the Club rows still seed the grid
            logging.getLogger("hktv.multi").warning(
                "stores: HKTV feed skipped: %s", str(error)[:120]
            )
        seeded: dict[str, ProductDetails] = {}
        for index in range(max(len(club_rows), len(hktv_rows))):  # the stores alternate
            for rows in (club_rows, hktv_rows):
                if index < len(rows):
                    seeded.setdefault(rows[index].product_id, rows[index])
        self.products = seeded
        logging.getLogger("hktv.multi").info(
            "stores: console feed loaded (%d products)", len(seeded)
        )

    def product(self, product_id: str) -> ProductDetails | None:
        return self.products.get(product_id)

    def reset_session(self, session_id: str) -> None:
        for store in self._stores.values():
            if hasattr(store, "reset_session"):
                store.reset_session(session_id)

    def recent_orders(self, limit: int = 6) -> list[Order]:
        return []  # the merchant overview's cross-user feed; this demo runs no portal


def agent_client() -> AsyncAnthropic | None:
    """The client for a local endpoint that speaks the Anthropic protocol, read from
    the same CLUB_LLM_* variables as the theclub vertical; None means the SDK's
    default client, from the environment."""
    base_url = os.environ.get("CLUB_LLM_BASE_URL")
    if not base_url:
        return None
    return AsyncAnthropic(
        base_url=base_url,
        api_key=os.environ.get("CLUB_LLM_API_KEY") or "local-keyless",
    )


backend = MultiStorefront({"theclub": club_storefront(), "hktv": HktvMallCatalog()})

agent = ShoppingAgent(
    backend=backend,
    client=agent_client(),
    skills_dir=REPO_ROOT / "shopping-agent" / "skills",
    config=build_shopping_config(),
)

host = build_storefront_host(
    title="HK stores demo API",
    example_root=DATA_DIR.parent,
    backend=backend,  # type: ignore[arg-type]  # MultiStorefront serves both roles
    agent=agent,
    memory_seeder=MemorySeeder(
        DATA_DIR / ("memory-seed.json" if not live_mode else "no-memory-seed.json"),
        marker=None if live_mode else DATA_DIR / ".memory-seeded.json",
    ),
)
app = host.app
_original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _lifespan(fastapi: FastAPI) -> AsyncIterator[None]:
    async with _original_lifespan(fastapi):
        await backend.load_feed()  # the grid reads both stores from the first request
        yield


app.router.lifespan_context = _lifespan
