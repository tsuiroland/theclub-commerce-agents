# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The Club example API — the 0.1 shell.

    uvicorn theclub.api.main:app --app-dir examples --reload --port 8000

Until the Magento GraphQL StorefrontBackend lands (Phase 1 of this example's roadmap),
the app runs on ACME's MockRetail as a stand-in catalog so the console, the The Club
config, and the shopping-agent skills can be exercised end-to-end. Phase 1 replaces
``backend`` and moves env handling out of the retail example's directory."""

from __future__ import annotations

from demo_common import REPO_ROOT, MemorySeeder, build_storefront_host, load_demo_env
from retail.api.mock_retail import DATA_DIR, MockRetail
from shopping_agent_runtime import ShoppingAgent

from .agent_config import build_shopping_config

# Placeholder: ACME's fixtures stand in for The Club's catalog. Env resolution
# (examples/retail/.env) rides along until this example gets its own data directory.
load_demo_env(DATA_DIR.parent)
backend = MockRetail()

agent = ShoppingAgent(
    backend=backend,
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
