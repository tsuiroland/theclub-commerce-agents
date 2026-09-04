# The Club (`theclub.com.hk`) deployment example

Status: **0.2, Phases 1, 2a, 2b, 3, and 4 shipped** (0.1 was the fork baseline and
initial example scaffold, tagged `v0.1.0`) — the live catalog over
`shop.theclub.com.hk`'s Magento GraphQL with Clubpoints pricing overlaid and
points-budget search, the member's own Club account signed in through the shop's
customer token with their real Clubpoints balance, a real cart on the shop with a
checkout handoff to the site itself, an earn/convert advisory grounded in The Club's
published rates, and a console of its own — The Club's near-black and Club-pink
identity, its grid, cart drawer, and add button all reading The Club. Policies and
fulfillment are not wired yet, and the tier name awaits the customer-group mapping
(the balance is real; `member_check` prints the group id that pins it).

The Club's transactional surface is `shop.theclub.com.hk` (Magento, GraphQL; the main
site is an AEM content portal), selling in dual currencies — HKD cash and Clubpoints
(CP) — to tiered members (blue, silver, gold, platinum, black), bilingually in English
and 繁體中文. The core repo is backend-agnostic; the work below is implementing its
`StorefrontBackend` over that surface.

## What's in 0.2

- `api/agent_config.py` — The Club identity: brand, bilingual points-first voice,
  dual-currency search guidance, Clubpoints/voucher/tier vocabulary in the policy
  grounding lexicon.
- `api/magento_catalog.py` — `ClubMagentoCatalog`, the `StorefrontBackend` over the live
  `shop.theclub.com.hk` Magento GraphQL: text search, product details, configurable
  families with variants, HKD cash prices, and (Phase 3) a real guest cart — add a
  variant under its family (`parent_sku`), update, remove, all on the shop's own quote.
  Anonymous GraphQL carries no Clubpoints redemption price (member-gated in Magento),
  so pure-redemption items (observed `CR-` SKU convention, HKD 0) carry a price note
  saying the points price shows after member login, and the cart refuses them rather
  than stage a 0-priced line. `checkout` hands off to the site's sign-in page plus each
  line's product page — the browser cannot resume this server-side quote, and the agent
  never places an order. The shop's cart lines hide the child identity of a variant
  (family sku only, no item-level sku, update/remove keyed by the base64-decoded quote
  id), so the backend tracks which written id each line is. Every unwired system
  answers that it is temporarily unavailable — its switch stays on per the backend
  contract.
- `api/aem_price_overlay.py` — `AemPriceOverlay`: the Clubpoints prices The Club's AEM
  shopping pages publish anonymously in their page models, indexed by sku (lazily
  loaded, per-page TTL, degraded gracefully when a page fails). The catalog merges a
  tile into its Magento result — a points-priced product leads with its CP price, cash
  items gain `earn_clubpoints` and vendor — and a `clubpoints_min`/`clubpoints_max`
  filter answers a points-budget search straight from the tiles, dearest first.
  Coverage is the merchandised pages, not the catalog; the member-gated Magento price
  (Phase 2b) remains the source of truth once a session carries a token.
- `api/live_storefront.py` — `LiveClubStorefront`: the live catalog dressed as the
  console's storefront, so the grid (seeded from the AEM tiles plus a page of cash
  results), the cart drawer, and the add button all read The Club under
  `CLUB_BACKEND=magento` (`CLUB_MAGENTO_URL`, `CLUB_MAGENTO_STORE` to override
  endpoint and store view; `CLUB_AEM_MODELS` to widen overlay coverage). Without it,
  ACME's `MockRetail` stands in as the demo.
- `api/earn_rates.py` — the earn/convert advisory's source of truth: rates The Club
  publishes as prose, curated with the page each was read from and the date checked —
  HKT services and Club Travel at 1 Clubpoint per HK$10, online shopping up to
  HK$2 = 1, the Citi card's 5%/HKT-bill rates, Citi Points conversion (270:1 Rewards,
  205:1 PremierMiles/Prestige/ULTIMA) and KrisFlyer miles (1 mile = 0.15 CP, 3,000
  minimum, 30,000/year cap). `rates_notes()` teaches them to the agent with
  citations; `python -m theclub.api.earn_rates` re-prints the pages' current text to
  diff against the curation. The assistant explains rates; conversions run on the
  signed-in portal.
- `storefront-web/` — The Club's own console: the shared storefront re-themed to the
  Club identity taken from theclub.com.hk's stylesheet (near-black `#1a1a1a` ink,
  Club pink `#ee315e` accents), serving the live feed.

## Roadmap

| Phase | Scope |
| --- | --- |
| 1 ✅ | Search + product details over Magento GraphQL (read-only; HKD pricing live, CP price member-gated) |
| 2a ✅ | Clubpoints pricing via the AEM page-model overlay; points-budget search; demo member context (`CLUB_DEMO_TIER`, `CLUB_DEMO_CP`) |
| 2b ✅ | The member's own login (`CLUB_MEMBER_EMAIL`/`CLUB_MEMBER_PASSWORD`, local .env only): the shop's customer token rides cart, profile, and order history, and the real Clubpoints balance reads off the shop's RewardPoints (`customer.reward_points.balance`) — the tier name awaits the customer-group mapping `member_check` prints |
| 3 ✅ | A real guest cart on the shop (add a variant under its family, update, remove), cash lines only — a Clubpoints redemption is refused for guests (its price is member-only) — and `checkout` hands off to the site's sign-in page plus each line's product page, never placing an order |
| 4 ✅ | Earn/convert advisory from The Club's published rates (`api/earn_rates.py`): HKT services, Club Travel, online shopping, the Citi card, Citi Points and KrisFlyer conversions — each citing its page. Shell/Esso and Marriott Bonvoy run earn offers but publish no rate pages; the advisory says so rather than guessing |

## Watching it work

The agent does not browse the site: its tools call two server-side surfaces —
`shop.theclub.com.hk/graphql` (Magento catalog) and The Club's AEM shopping page models
(Clubpoints prices) — and every fetch prints a `theclub <-` line in the API's console
(`CLUB_TRACE=0` to silence):

    theclub <- AEM https://www.theclub.com.hk/shopping/en/lc/clubpoints-zone.model.json (313 tiles, 313 points-priced)
    theclub <- Magento GraphQL SearchProducts {'q': 'headphone', 'limit': 8} (1329 ms)

The rest of the picture: the console shows the working labels ("Looking for items…")
and the product cards — their images are hosted on shop.theclub.com.hk, live proof of
where the data came from — and the raw event stream (tool calls with their inputs,
components, text) is `POST /api/chat`'s SSE body, watchable with:

    curl -N -X POST http://localhost:8004/api/chat -H "content-type: application/json" \
      -H "cookie: <the console's session cookie>" -d '{"message": "Wellcome voucher"}'

## Run

Needs `ANTHROPIC_API_KEY` (the environment, repo-root `.env`, or `examples/retail/.env`).
The one-command console (The Club storefront on :3004, the API on :8004) — everything
in `.env` already:

    .venv/bin/python scripts/run_demo.py theclub

or the API alone, pointed at a storefront you run yourself:

    CLUB_BACKEND=magento .venv/bin/python -m uvicorn theclub.api.main:app \
      --app-dir examples --reload --port 8004
    CLUB_MAGENTO_STORE=zh_Hant_HK ...   # 繁體中文 store view
    CLUB_AEM_MODELS=url1,url2 ...       # more AEM pages to price from

In the chat, *"add the Clicks keyboard, Spice color"* writes the shop's real quote
(watch the `theclub <- AddProductsToCart` trace), *"make it one"* and *"remove it"*
update and remove it, and *"check out"* stages a summary whose buttons open the site.
The grid, the cart drawer, and the add button read the same live feed.

### Your own Club account

Sign the assistant in as yourself (guest mode answers as no one, and a points
redemption is refused with the honest member-only message):

    # .env — never committed, never logged
    CLUB_MEMBER_EMAIL=you@example.com
    CLUB_MEMBER_PASSWORD=your-password

Your cart, your profile, your order history, and your real Clubpoints balance then
run on the shop's customer token (refreshed once when it expires). Check the whole
member surface first:

    PYTHONPATH=examples .venv/bin/python -m theclub.api.member_check

It prints your name, Clubpoints balance (and cash equivalent), customer group id,
cart, and recent orders — and whether the shop serves the member-gated Clubpoints
field to the token — and no secrets. The group id is the tier lead: once you see
yours, pin it in `TIER_GROUPS` in `api/magento_catalog.py` and the member context
carries your tier too. `CLUB_DEMO_TIER` / `CLUB_DEMO_CP` stand in for tier and
balance only while signed out.

### A local LLM

`CLUB_LLM_BASE_URL` points the assistant at a local endpoint that speaks the Anthropic
Messages protocol (`/v1/messages`, streaming, tool use, thinking — vLLM and LM Studio
style gateways expose it); key-less servers take a stand-in key. `CLUB_LLM_MODEL` names
the model the endpoint serves, so the config requests it. The turn loop is chatty — a
~20KB system prompt, 21 tools, thinking per round — so expect the first budget question
to take a while on smaller local deployments; a mid-size reasoning model handles the
tool loop, and smaller ones may need `thinking_effort=None` (`CLUB_LLM_THINKING=0`) or a
shorter flow set; with thinking off, a budget question on a 27B local deployment ran
about 4× faster and still presented grounded picks.

Then ask, in the console's chat, things like *"what can I get for 5,000 Clubpoints?"*
(a budget search over the AEM tiles), *"Wellcome voucher"* (a budget search narrowed by
words), *"espresso machine"* (Magento text search), *"show me the Nothing Headphone"* —
the overlay prices it in CP when a tile carries it — or *"how do I earn Clubpoints
faster?"* and *"what's the Citi Points conversion rate?"* (the earn/convert advisory,
answered from `earn_rates.py` with the page cited). Recommendations and suggestion
chips name only products a tool returned this session, by their real titles.
