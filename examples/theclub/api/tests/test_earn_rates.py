# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The curated earn/convert rates: every entry cites the page it was read from and
when it was checked, and the notes the agent teaches carry all three together."""

from __future__ import annotations

import re

from ..earn_rates import PORTAL, CONVERSION_RATES, EARN_RATES, _rate_lines, rates_notes


def test_every_rate_cites_its_page_and_check_date() -> None:
    for rate in (*EARN_RATES, *CONVERSION_RATES):
        assert rate.partner and rate.rate
        assert rate.source_path.startswith("/") and rate.source_path.endswith(".html")
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", rate.checked)


def test_rates_notes_carry_rates_and_sources() -> None:
    notes = rates_notes()
    for rate in (*EARN_RATES, *CONVERSION_RATES):
        assert rate.partner in notes
        assert f"{PORTAL}{rate.source_path}" in notes
        assert "checked" in notes
    # The conversion ratios the pages state, not vague pointers.
    assert "270 Citi Points = 1 Clubpoint" in notes
    assert "1 KrisFlyer mile = 0.15 Clubpoints" in notes
    # The assistant explains rates; the portal performs conversions.
    assert "does not perform conversions" in notes


def test_rate_lines_extracts_points_prose_and_the_icon_placeholder() -> None:
    model = {
        "text": "<b>270 Citi Points &#61; 1 Clubpoint</b>. Earn :P:5 per HK$100 of spend."
    }
    lines = _rate_lines(model)
    assert any("270" in line for line in lines)
    assert any("HK$100" in line for line in lines)
