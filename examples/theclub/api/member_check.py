# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""One command to try your own Club login against the shop — no console needed:

    PYTHONPATH=examples .venv/bin/python -m theclub.api.member_check

Reads CLUB_MEMBER_EMAIL and CLUB_MEMBER_PASSWORD from the repo-root .env (or the
environment), signs in, and reports what the member token unlocks: your name, your
cart, your orders, and whether the member-gated Clubpoints price comes back on a
redemption product. Prints nothing secret: no password, no token."""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

from .magento_catalog import SEARCH_QUERY, ClubMagentoCatalog


async def main() -> int:
    load_dotenv(".env")
    email = os.environ.get("CLUB_MEMBER_EMAIL", "")
    password = os.environ.get("CLUB_MEMBER_PASSWORD", "")
    if not email or not password:
        print("Set CLUB_MEMBER_EMAIL and CLUB_MEMBER_PASSWORD in .env first.")
        return 1

    catalog = ClubMagentoCatalog(email=email, password=password)
    print(f"1. signing in as {email} …")
    try:
        await catalog._ensure_token()
    except RuntimeError as error:
        print(f"   sign-in failed: {str(error)[:160]}")
        return 1
    print("   signed in")

    from shopping_agent import ShoppingSessionContext

    session = ShoppingSessionContext(session_id="member-check", user_id="check")

    preferences = await catalog.get_preferences(session)
    print(f"2. profile: {preferences.display_name} ({preferences.preferences.get('member_email')})")

    cart = await catalog.get_cart(session)
    print(f"3. your cart: {cart.item_count} item(s), subtotal HK${cart.subtotal}")

    try:
        orders = await catalog.get_orders(session)
        print(f"4. recent orders: {len(orders)}")
        for order in orders[:3]:
            print(f"   {order.order_id}  {order.status.value:<10} HK${order.total}")
    except RuntimeError as error:
        print(f"4. orders: the shop declined ({str(error)[:120]})")

    data = await catalog._query(
        SEARCH_QUERY, {"q": "Wellcome voucher", "limit": 3}, "SearchProducts", authed=True
    )
    print("5. Clubpoints price while signed in (the member-gated field):")
    for item in data["products"]["items"]:
        print(f"   {item['sku']:<24} clubpoints={item.get('clubpoints')}")
    print("   (0 means the shop still gates the redemption price from this token;")
    print("    the AEM overlay keeps answering it in the meantime.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
