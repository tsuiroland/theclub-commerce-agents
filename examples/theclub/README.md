# The Club (`theclub.com.hk`) deployment example

Status: **0.1, Phase 1 shipped** — the The Club agent config, a runnable console, and
the live read-only catalog over `shop.theclub.com.hk`'s Magento GraphQL. Cart, orders,
policies, fulfillment, and member identity are not wired yet.

The Club's transactional surface is `shop.theclub.com.hk` (Magento, GraphQL; the main
site is an AEM content portal), selling in dual currencies — HKD cash and Clubpoints
(CP) — to tiered members (blue, silver, gold, platinum, black), bilingually in English
and 繁體中文. The core repo is backend-agnostic; the work below is implementing its
`StorefrontBackend` over that surface.

## What's in 0.1

- `api/agent_config.py` — The Club identity: brand, bilingual points-first voice,
  dual-currency search guidance, Clubpoints/voucher/tier vocabulary in the policy
  grounding lexicon.
- `api/magento_catalog.py` — `ClubMagentoCatalog`, a read-only `StorefrontBackend` over
  the live `shop.theclub.com.hk` Magento GraphQL: text search, product details,
  configurable families with variants, HKD cash prices. Anonymous GraphQL carries no
  Clubpoints redemption price (member-gated in Magento), so pure-redemption items
  (observed `CR-` SKU convention, HKD 0) carry a price note saying the points price
  shows after member login. Every unwired system answers that it is temporarily
  unavailable — its switch stays on per the backend contract.
- `api/main.py` — runnable console API. The console chrome stays on ACME's `MockRetail`;
  the assistant's backend switches to the live catalog with `CLUB_BACKEND=magento`
  (`CLUB_MAGENTO_URL`, `CLUB_MAGENTO_STORE` to override endpoint and store view).

## Roadmap

| Phase | Scope |
| --- | --- |
| 1 ✅ | Search + product details over Magento GraphQL (read-only; HKD pricing live, CP price member-gated) |
| 2 | Member identity: tier + CP balance; personalized, points-budget search (AEM page models carry anonymous dual pricing as an interim source) |
| 3 | Cart + checkout handoff to Magento hosted checkout — never an autonomous purchase |
| 4 | Points earn/convert advisory (HKT bills, Citi ThankYou, Shell/Esso, Marriott Bonvoy, KrisFlyer) |

## Run

    uvicorn theclub.api.main:app --app-dir examples --reload --port 8000

Needs `ANTHROPIC_API_KEY` (repo-root `.env`, or `examples/retail/.env` until Phase 2
moves env handling into this example). Put the assistant on the live catalog with:

    CLUB_BACKEND=magento uvicorn theclub.api.main:app --app-dir examples --reload --port 8000
    CLUB_MAGENTO_STORE=zh_Hant_HK ...   # 繁體中文 store view
