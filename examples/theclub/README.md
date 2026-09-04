# The Club (`theclub.com.hk`) deployment example

Status: **0.1, Phases 1 and 2a shipped** — the The Club agent config, a runnable
console, the live read-only catalog over `shop.theclub.com.hk`'s Magento GraphQL, and
Clubpoints pricing overlaid from The Club's AEM shopping pages, with points-budget
search and demo member context. Cart, orders, policies, fulfillment, and the real
member token (Phase 2b) are not wired yet.

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
- `api/aem_price_overlay.py` — `AemPriceOverlay`: the Clubpoints prices The Club's AEM
  shopping pages publish anonymously in their page models, indexed by sku (lazily
  loaded, per-page TTL, degraded gracefully when a page fails). The catalog merges a
  tile into its Magento result — a points-priced product leads with its CP price, cash
  items gain `earn_clubpoints` and vendor — and a `clubpoints_min`/`clubpoints_max`
  filter answers a points-budget search straight from the tiles, dearest first.
  Coverage is the merchandised pages, not the catalog; the member-gated Magento price
  (Phase 2b) remains the source of truth once a session carries a token.
- `api/main.py` — runnable console API. The console chrome stays on ACME's `MockRetail`;
  the assistant's backend switches to the live catalog with `CLUB_BACKEND=magento`
  (`CLUB_MAGENTO_URL`, `CLUB_MAGENTO_STORE` to override endpoint and store view;
  `CLUB_AEM_MODELS` to widen overlay coverage; `CLUB_DEMO_TIER`/`CLUB_DEMO_CP` demo
  member context standing in for the Phase 2b member token).

## Roadmap

| Phase | Scope |
| --- | --- |
| 1 ✅ | Search + product details over Magento GraphQL (read-only; HKD pricing live, CP price member-gated) |
| 2a ✅ | Clubpoints pricing via the AEM page-model overlay; points-budget search; demo member context (`CLUB_DEMO_TIER`, `CLUB_DEMO_CP`) |
| 2b | The member token: real tier + CP balance from the member-gated Magento price and the loyalty service, replacing the overlay's stand-in and the demo knobs |
| 3 | Cart + checkout handoff to Magento hosted checkout — never an autonomous purchase |
| 4 | Points earn/convert advisory (HKT bills, Citi ThankYou, Shell/Esso, Marriott Bonvoy, KrisFlyer) |

## Run

Needs `ANTHROPIC_API_KEY` (the environment, repo-root `.env`, or `examples/retail/.env`).
The one-command console (ACME's storefront-web via symlink on :3004, the API on :8004):

    CLUB_BACKEND=magento CLUB_DEMO_TIER=gold CLUB_DEMO_CP=10000 \
      .venv/bin/python scripts/run_demo.py theclub

or the API alone, pointed at a storefront you run yourself:

    CLUB_BACKEND=magento .venv/bin/python -m uvicorn theclub.api.main:app \
      --app-dir examples --reload --port 8004
    CLUB_MAGENTO_STORE=zh_Hant_HK ...   # 繁體中文 store view
    CLUB_AEM_MODELS=url1,url2 ...       # more AEM pages to price from

`CLUB_DEMO_TIER`/`CLUB_DEMO_CP` stand in for the member experience while the Phase 2b
token is outstanding.

Then ask, in the console's chat, things like *"what can I get for 5,000 Clubpoints?"*
(a budget search over the AEM tiles), *"Wellcome voucher"* (a budget search narrowed by
words), *"espresso machine"* (Magento text search), or *"show me the Nothing Headphone"* —
the overlay prices it in CP when a tile carries it. The console's own product grid and
cart button still run ACME's mock fixtures; only the assistant reads The Club.
