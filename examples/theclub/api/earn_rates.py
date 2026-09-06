# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The Club's published earn/convert rates, curated from the portal's own pages.

The portal publishes rates as prose, not an API, so this module is the agent's
source of truth: every rate names the page it was read from and the date it was
checked, and ``refresh()`` re-prints those pages' current text to diff against the
curation (a partner changing a rate shows up as a mismatch to reconcile by hand)."""

from __future__ import annotations

import re
from dataclasses import dataclass

PORTAL = "https://www.theclub.com.hk"


@dataclass(frozen=True)
class EarnRate:
    partner: str
    rate: str  # what a member earns or converts, stated plainly
    source_path: str  # the portal page the rate was read from
    checked: str  # when the page was last checked, ISO date


EARN_RATES: tuple[EarnRate, ...] = (
    EarnRate(
        partner="HKT services (1O1O, csl, Netvigator, Smart Living)",
        rate="1 Clubpoint per HK$10 of eligible spending on designated HKT services",
        source_path="/en/earn/telecommunications.html",
        checked="2026-09-04",
    ),
    EarnRate(
        partner="Club Travel",
        rate="1 Clubpoint per HK$10 on worldwide hotels and flights booked through Club Travel",
        source_path="/en/explore/what-is-club-point.html",
        checked="2026-09-04",
    ),
    EarnRate(
        partner="Club online shopping",
        rate="up to HK$2 = 1 Clubpoint shopping 150+ merchants through The Club",
        source_path="/en/explore/what-is-club-point.html",
        checked="2026-09-04",
    ),
    EarnRate(
        partner="Citi The Club Credit Card",
        rate=(
            "HK$100 = 5 Clubpoints on general spend; HK$100 = 20 Clubpoints (a 4% "
            "rebate) on monthly HKT bills auto-billed to the card"
        ),
        source_path="/en/citi-the-club-credit-card.html",
        checked="2026-09-04",
    ),
)

CONVERSION_RATES: tuple[EarnRate, ...] = (
    EarnRate(
        partner="Citi Points to Clubpoints",
        rate=(
            "Citi Rewards Card: 270 Citi Points = 1 Clubpoint; Citi PremierMiles, "
            "Prestige, and ULTIMA Cards: 205 Citi Points = 1 Clubpoint; minimum 500 "
            "Clubpoints per redemption, once per day per cardholder"
        ),
        source_path="/en/earn/citi-points-conversion.html",
        checked="2026-09-04",
    ),
    EarnRate(
        partner="KrisFlyer miles to Clubpoints",
        rate=(
            "1 KrisFlyer mile = 0.15 Clubpoints (rounded down); minimum 3,000 miles "
            "(450 Clubpoints) per request, maximum 30,000 miles (4,500 Clubpoints) "
            "per calendar year"
        ),
        source_path="/en/our-partners/krisflyer-clubpoints-conversion.html",
        checked="2026-09-04",
    ),
)


def rates_notes() -> str:
    """The earn/convert paragraph the agent config teaches, rates and citations only."""
    lines = [
        "Earn and convert rates The Club publishes (answer earn/convert questions "
        "from these, citing the page; when a rate is not listed here, say so and "
        "point to the member's own account pages rather than guessing):"
    ]
    lines += [
        f"- {r.partner}: {r.rate} [{PORTAL}{r.source_path}, checked {r.checked}]"
        for r in EARN_RATES
    ]
    lines += [
        f"- {r.partner}: {r.rate} [{PORTAL}{r.source_path}, checked {r.checked}]"
        for r in CONVERSION_RATES
    ]
    lines.append(
        "Conversions themselves run on the signed-in portal "
        f"({PORTAL}/en/my-account/manage-my-clubpoint/convert-to-clubpoints.html); "
        "this assistant explains rates, it does not perform conversions."
    )
    return "\n".join(lines)


async def refresh() -> None:
    """Re-print the source pages' rate-bearing text to diff against the curation
    above (the model.json variant of each page carries the same prose as text)."""
    import httpx

    async with httpx.AsyncClient(timeout=20.0) as client:
        for rate in (*EARN_RATES, *CONVERSION_RATES):
            page = rate.source_path.removesuffix(".html")  # .model.json replaces .html
            url = f"{PORTAL}{page}.model.json"
            response = await client.get(url)
            print(f"== {rate.partner} ({url}, checked {rate.checked})")
            if response.status_code != 200:
                print(f"   page moved or gone: HTTP {response.status_code}")
                continue
            for line in _rate_lines(response.json()):
                print(f"   {line}")


_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def _rate_lines(model: dict) -> list[str]:
    """The page model's sentences that mention points/HK$ — prose, kept honest."""
    text = _SPACE.sub(" ", _TAG.sub(" ", str(model)))
    text = text.replace(":P:", " Clubpoint ")  # the portal's points-icon placeholder
    sentences = [s.strip() for s in text.split(". ")]
    return [
        s[:220]
        for s in sentences
        if re.search(r"(?i)clubpoint|per HK\$|HK\$\d+ ?=|convert", s) and len(s) > 30
    ][:6]


if __name__ == "__main__":
    import asyncio

    asyncio.run(refresh())
