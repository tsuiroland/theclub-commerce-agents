# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The Club example API — the 0.1 shell plus the live Phase 1 catalog.

    uvicorn theclub.api.main:app --app-dir examples --reload --port 8000

The console's storefront chrome (product grid, add-to-cart button) stays on ACME's
MockRetail until The Club's own cart lands in Phase 3. The assistant's backend is
``CLUB_BACKEND=magento`` away from the live shop.theclub.com.hk catalog:

    CLUB_BACKEND=magento                       # HKD cash prices, anonymous
    CLUB_MAGENTO_STORE=zh_Hant_HK              # 繁體中文 catalog, if the members use it

Env resolution (examples/retail/.env) rides along with the mock fixtures until this
example gets its own data directory."""

from __future__ import annotations

import os

from demo_common import REPO_ROOT, MemorySeeder, build_storefront_host, load_demo_env
from retail.api.mock_retail import DATA_DIR, MockRetail
from shopping_agent import StorefrontBackend
from shopping_agent_runtime import ShoppingAgent

from .agent_config import build_shopping_config
from .magento_catalog import DEFAULT_GRAPHQL_URL, DEFAULT_STORE, ClubMagentoCatalog

load_demo_env(DATA_DIR.parent)
backend = MockRetail()


def agent_backend() -> StorefrontBackend:
    if os.environ.get("CLUB_BACKEND") == "magento":
        return ClubMagentoCatalog(
            graphql_url=os.environ.get("CLUB_MAGENTO_URL", DEFAULT_GRAPHQL_URL),
            store_code=os.environ.get("CLUB_MAGENTO_STORE", DEFAULT_STORE),
        )
    return backend  # the stand-in: the same fixtures the console grid shows


agent = ShoppingAgent(
    backend=agent_backend(),
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
