# The Club (`theclub.com.hk`) deployment example

Status: **0.1 shell** — the The Club agent config and a runnable console on a
placeholder backend. No Club systems are wired yet.

The Club's transactional surface is `shop.theclub.com.hk` (Magento, GraphQL; the main
site is an AEM content portal), selling in dual currencies — HKD cash and Clubpoints
(CP) — to tiered members (blue, silver, gold, platinum, black), bilingually in English
and 繁體中文. The core repo is backend-agnostic; the work below is implementing its
`StorefrontBackend` over that surface.

## What's in 0.1

- `api/agent_config.py` — The Club identity: brand, bilingual points-first voice,
  dual-currency search guidance, Clubpoints/voucher/tier vocabulary in the policy
  grounding lexicon.
- `api/main.py` — runnable console API on ACME's `MockRetail` as a stand-in catalog.

## Roadmap

| Phase | Scope |
| --- | --- |
| 1 | Search + product details over Magento GraphQL (read-only, dual HKD/CP pricing) |
| 2 | Member identity: tier + CP balance; personalized, points-budget search |
| 3 | Cart + checkout handoff to Magento hosted checkout — never an autonomous purchase |
| 4 | Points earn/convert advisory (HKT bills, Citi ThankYou, Shell/Esso, Marriott Bonvoy, KrisFlyer) |

## Run

    uvicorn theclub.api.main:app --app-dir examples --reload --port 8000

Needs `ANTHROPIC_API_KEY` (repo-root `.env`, or `examples/retail/.env` until Phase 2
moves env handling into this example).
