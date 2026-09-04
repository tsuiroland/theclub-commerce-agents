# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The Club deployment's shopping agent config. The Club sells in two currencies — HKD
cash and Clubpoints (CP) — to tiered members (blue, silver, gold, platinum, black) in
English and 繁體中文; the notes and lexicons below teach the generic agent those facts.
The backend itself is Phase 2 of this example's roadmap."""

from __future__ import annotations

import os

from shopping_agent import ShoppingAgentConfig

from .earn_rates import rates_notes

# The Club policy vocabulary on top of the generic retail lexicon: points, redemptions,
# vouchers, and tiers all route policy questions through the grounding gate.
POLICY_INTENT_TERMS = (
    # Generic retail terms, upstream defaults.
    "return",
    "returns",
    "refund",
    "refunds",
    "exchange",
    "exchanges",
    "warranty",
    "guarantee",
    "cancel",
    "cancellation",
    "restocking",
    "fee",
    "fees",
    "shipping cost",
    "shipping costs",
    "delivery cost",
    "price match",
    "price lock",
    "membership",
    "subscription",
    "contract",
    "policy",
    "policies",
    "terms",
    # The Club terms.
    "clubpoints",
    "club points",
    "points",
    "redemption",
    "redeem",
    "e-voucher",
    "evoucher",
    "voucher",
    "tier",
)


def build_shopping_config() -> ShoppingAgentConfig:
    # CLUB_LLM_MODEL names the model a local Anthropic-protocol endpoint serves (see
    # main.py's CLUB_LLM_BASE_URL); without it, the Anthropic default the config names.
    # CLUB_LLM_THINKING=0 disables thinking for endpoints whose reasoning traces cost
    # more latency than they buy accuracy on this tool loop.
    thinking = None if os.environ.get("CLUB_LLM_THINKING") == "0" else "low"
    return ShoppingAgentConfig(
        model=os.environ.get("CLUB_LLM_MODEL") or "claude-sonnet-5",
        thinking_effort=thinking,
        brand_name="The Club",
        assistant_name="the Club shopping assistant",
        brand_voice=(
            "warm, concise, and plain about trade-offs; leads with the Clubpoints price "
            "when a product has one; replies in the member's language, English or 繁體中文"
        ),
        domain_search_notes=(
            "Every product carries dual pricing: an HKD cash price and/or a Clubpoints "
            "(CP) redemption price, and some offers are exclusive to a member tier "
            "(blue, silver, gold, platinum, black). A points-priced product's price is "
            "in CP with its cash equivalent in attributes.cash_price_hkd; a cash "
            "product's price is HKD, sometimes with attributes.earn_clubpoints on top. "
            "Members often search by points budget — pass the budget as the filter "
            "attribute clubpoints_max (a plain number: the cap to spend under, so "
            "'10,000 points' means clubpoints_max=10000), and use clubpoints_min only "
            "when the member wants to spend at least that much. A points budget is a "
            "CP figure, not a cash amount. Recommend and suggest only products a tool "
            "returned this session, by their real title; never name a product or SKU "
            "the catalog did not return.\n" + rates_notes()
        ),
        policy_intent_terms=POLICY_INTENT_TERMS,
    )
