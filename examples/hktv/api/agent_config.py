# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The multi-store deployment's shopping agent config: one assistant over The Club
and HKTV Mall, selling in HKD (plus The Club's Clubpoints) bilingually in English
and 繁體中文. The Club's policy vocabulary and earn-rate notes ride along because
that side of the federation carries them."""

from __future__ import annotations

import os

from commerce_common.config import DEFAULT_MEMORY_MODEL
from shopping_agent import ShoppingAgentConfig
from theclub.api.agent_config import POLICY_INTENT_TERMS
from theclub.api.earn_rates import rates_notes


def build_shopping_config() -> ShoppingAgentConfig:
    # The same local-LLM env the theclub vertical reads (see its agent_config):
    # one CLUB_LLM_* family for the Hong Kong examples.
    thinking = None if os.environ.get("CLUB_LLM_THINKING") == "0" else "low"
    return ShoppingAgentConfig(
        model=os.environ.get("CLUB_LLM_MODEL") or "claude-sonnet-5",
        memory_model=(
            os.environ.get("CLUB_LLM_MEMORY_MODEL")
            or os.environ.get("CLUB_LLM_MODEL")
            or DEFAULT_MEMORY_MODEL
        ),
        thinking_effort=thinking,
        brand_name="The Club and HKTV Mall",
        assistant_name="your Hong Kong shopping assistant",
        brand_voice=(
            "plain and specific about trade-offs; names the store behind every pick "
            "and its price there (HKD cash or Clubpoints); replies in the member's "
            "language, English or 繁體中文"
        ),
        domain_search_notes=(
            "Search spans two stores and every result names its store in "
            "attributes.store: The Club (HKD cash and Clubpoints redemption prices, "
            "member tiers, earn rates) and HKTV Mall (HKD, a per-sku loyalty earn in "
            "attributes.loyalty_point). Product ids are namespaced per store — "
            "theclub:SKU and hktv:CODE — and a family id from The Club names its "
            "variants; write ids exactly as the tool returned them, never a raw sku. "
            "A Clubpoints budget (filter attribute clubpoints_max, a plain CP number) "
            "applies to The Club's side only. HKTV Mall's cart and order history are "
            "not wired: say so plainly instead of offering them; The Club's cart and "
            "checkout handoff work, and the assistant never places an order.\n" + rates_notes()
        ),
        policy_intent_terms=POLICY_INTENT_TERMS,
    )
