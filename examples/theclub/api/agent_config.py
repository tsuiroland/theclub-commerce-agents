# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The Club deployment's shopping agent config. The Club sells in two currencies — HKD
cash and Clubpoints (CP) — to tiered members (blue, silver, gold, platinum, black) in
English and 繁體中文; the notes and lexicons below teach the generic agent those facts.
The backend itself is Phase 2 of this example's roadmap."""

from __future__ import annotations

from shopping_agent import ShoppingAgentConfig

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
    return ShoppingAgentConfig(
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
            "Members often search by points budget — pass it as the filter attributes "
            "clubpoints_min and clubpoints_max (plain numbers), and treat a CP figure "
            "such as '10,000 points' as the budget to spend, not a cash amount."
        ),
        policy_intent_terms=POLICY_INTENT_TERMS,
    )
