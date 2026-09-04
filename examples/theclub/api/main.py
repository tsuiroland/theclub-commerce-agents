# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The Club example API — the live Club under every surface of the console.

    uvicorn theclub.api.main:app --app-dir examples --reload --port 8004

Under ``CLUB_BACKEND=magento`` the console itself reads The Club: the grid lists the
AEM tiles' merchandised feed plus a page of cash results, the cart drawer and the add
button write the shop's own quote, and the chat runs on the same catalog with the
points overlay. Without it, ACME's MockRetail stands in as before. The member's own
account (``CLUB_MEMBER_EMAIL``/``CLUB_MEMBER_PASSWORD``) signs the shop's customer
token in for cart, profile, and orders; the demo tier and balance stand in only for
guests. Env resolution rides along with the retail example's .env until this example
gets its own data directory."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from anthropic import AsyncAnthropic
from fastapi import FastAPI

from demo_common import REPO_ROOT, MemorySeeder, build_storefront_host, load_demo_env
from retail.api.mock_retail import DATA_DIR, MockRetail
from shopping_agent_runtime import ShoppingAgent

from .agent_config import build_shopping_config
from .aem_price_overlay import DEFAULT_MODEL_URLS, AemPriceOverlay
from .live_storefront import LiveClubStorefront
from .magento_catalog import DEFAULT_GRAPHQL_URL, DEFAULT_STORE

load_demo_env(DATA_DIR.parent)

# Every fetch of the live Club surfaces prints a "theclub <-" line (GraphQL queries,
# AEM price pages), so a running console answers where its answers come from. Silence
# with CLUB_TRACE=0.
_trace = logging.getLogger("theclub")
if os.environ.get("CLUB_TRACE") != "0" and not _trace.handlers:
    _trace.addHandler(logging.StreamHandler())
    _trace.setLevel(logging.INFO)

live_mode = os.environ.get("CLUB_BACKEND") == "magento"


def live_storefront() -> LiveClubStorefront | None:
    if not live_mode:
        return None
    models = tuple(
        url.strip()
        for url in os.environ.get("CLUB_AEM_MODELS", ",".join(DEFAULT_MODEL_URLS)).split(",")
        if url.strip()
    )
    balance = os.environ.get("CLUB_DEMO_CP", "")
    member = os.environ.get("CLUB_MEMBER_EMAIL")
    return LiveClubStorefront(
        graphql_url=os.environ.get("CLUB_MAGENTO_URL", DEFAULT_GRAPHQL_URL),
        store_code=os.environ.get("CLUB_MAGENTO_STORE", DEFAULT_STORE),
        overlay=AemPriceOverlay(model_urls=models or DEFAULT_MODEL_URLS),
        demo_tier=None if member else os.environ.get("CLUB_DEMO_TIER"),
        demo_clubpoints=None if member else (int(balance) if balance.isdigit() else None),
        email=member,
        password=os.environ.get("CLUB_MEMBER_PASSWORD"),
    )


backend = live_storefront() or MockRetail()


def agent_client() -> AsyncAnthropic | None:
    """The client for a local endpoint that speaks the Anthropic protocol (streaming,
    tool use, thinking all verified against it); key-less ones take a stand-in key.
    None means the SDK's default client, from the environment."""
    base_url = os.environ.get("CLUB_LLM_BASE_URL")
    if not base_url:
        return None
    return AsyncAnthropic(
        base_url=base_url,
        api_key=os.environ.get("CLUB_LLM_API_KEY") or "local-keyless",
    )


agent = ShoppingAgent(
    backend=backend,
    client=agent_client(),
    skills_dir=REPO_ROOT / "shopping-agent" / "skills",
    config=build_shopping_config(),
)

host = build_storefront_host(
    title="The Club demo API",
    example_root=DATA_DIR.parent,
    backend=backend,  # type: ignore[arg-type]  # LiveClubStorefront serves both roles
    agent=agent,
    # ACME's seeded memories suit the mock; the live Club brings no Priya with it.
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
        if isinstance(backend, LiveClubStorefront):
            await backend.load_feed()  # the grid reads The Club from the first request
        yield


app.router.lifespan_context = _lifespan
