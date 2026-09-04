# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The Club example API — the live catalog (Phase 1) under the points overlay (Phase 2a).

    uvicorn theclub.api.main:app --app-dir examples --reload --port 8000

The console's storefront chrome (product grid, add-to-cart button) stays on ACME's
MockRetail until The Club's own cart lands in Phase 3. The assistant's backend is
``CLUB_BACKEND=magento`` away from the live shop.theclub.com.hk catalog with the AEM
points overlay attached, and demo member knobs standing in for the member token:

    CLUB_BACKEND=magento                       # live catalog + points overlay
    CLUB_MAGENTO_STORE=zh_Hant_HK              # 繁體中文 catalog, if the members use it
    CLUB_AEM_MODELS=url1,url2                  # more AEM pages to price from
    CLUB_DEMO_TIER=gold CLUB_DEMO_CP=10000     # demo member context (Phase 2b replaces)
    CLUB_LLM_BASE_URL=http://localhost:8000    # a local Anthropic-protocol endpoint
    CLUB_LLM_MODEL=its-model-name              #   (key-less; CLUB_LLM_API_KEY if it wants one)

Env resolution (examples/retail/.env) rides along with the mock fixtures until this
example gets its own data directory."""

from __future__ import annotations

import os

from anthropic import AsyncAnthropic

from demo_common import REPO_ROOT, MemorySeeder, build_storefront_host, load_demo_env
from retail.api.mock_retail import DATA_DIR, MockRetail
from shopping_agent import StorefrontBackend
from shopping_agent_runtime import ShoppingAgent

from .aem_price_overlay import DEFAULT_MODEL_URLS, AemPriceOverlay
from .agent_config import build_shopping_config
from .magento_catalog import DEFAULT_GRAPHQL_URL, DEFAULT_STORE, ClubMagentoCatalog

load_demo_env(DATA_DIR.parent)
backend = MockRetail()


def agent_backend() -> StorefrontBackend:
    if os.environ.get("CLUB_BACKEND") == "magento":
        models = tuple(
            url.strip()
            for url in os.environ.get("CLUB_AEM_MODELS", ",".join(DEFAULT_MODEL_URLS)).split(",")
            if url.strip()
        )
        balance = os.environ.get("CLUB_DEMO_CP", "")
        return ClubMagentoCatalog(
            graphql_url=os.environ.get("CLUB_MAGENTO_URL", DEFAULT_GRAPHQL_URL),
            store_code=os.environ.get("CLUB_MAGENTO_STORE", DEFAULT_STORE),
            overlay=AemPriceOverlay(model_urls=models or DEFAULT_MODEL_URLS),
            demo_tier=os.environ.get("CLUB_DEMO_TIER"),
            demo_clubpoints=int(balance) if balance.isdigit() else None,
        )
    return backend  # the stand-in: the same fixtures the console grid shows


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
    backend=agent_backend(),
    client=agent_client(),
    skills_dir=REPO_ROOT / "shopping-agent" / "skills",
    config=build_shopping_config(),
)

host = build_storefront_host(
    title="The Club demo API (0.1)",
    example_root=DATA_DIR.parent,
    backend=backend,
    agent=agent,
    memory_seeder=MemorySeeder(
        DATA_DIR / "memory-seed.json", marker=DATA_DIR / ".memory-seeded.json"
    ),
)
app = host.app
