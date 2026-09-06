# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The multi-store config's search notes: the two stores, the namespaced ids, and
the cross-store comparison rule a same-product answer follows."""

from __future__ import annotations

from ..agent_config import build_shopping_config


def test_notes_teach_both_stores_and_the_namespaced_ids() -> None:
    notes = build_shopping_config().domain_search_notes
    assert "attributes.store" in notes
    assert "theclub:SKU" in notes and "hktv:CODE" in notes
    assert "HKTV Mall" in notes and "The Club" in notes


def test_notes_teach_the_cross_store_comparison_rule() -> None:
    notes = build_shopping_config().domain_search_notes
    assert "present_comparison" in notes
    assert "matching brand and model" in notes
    assert "entry per store" in notes
    # the recommendation must respect where the member can act
    assert "HKTV Mall has none" in notes
