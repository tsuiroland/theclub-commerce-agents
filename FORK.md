# Fork notes

This is `tsuiroland/theclub-commerce-agents`, a fork of
[anthropics/commerce-agents](https://github.com/anthropics/commerce-agents) at commit
`fd4d592` (upstream `main`; also tagged `upstream-baseline` in local clones).

Purpose: the baseline for a The Club ([theclub.com.hk](https://theclub.com.hk))
shopping agent. The upstream repository is an unmaintained Apache 2.0 reference
implementation, so this fork owns all further development. `main` stays a pristine
mirror of upstream; The Club work happens on `release/0.1` and later release branches.

## 0.1 scope

- Fork + `release/0.1` branch (this branch)
- `examples/theclub/`: The Club agent config — brand identity, bilingual
  (English / 繁體中文) points-first voice, dual HKD / Clubpoints search guidance, points
  vocabulary in the policy grounding lexicon — plus a runnable console mounted on a
  placeholder backend
- Core packages (`commerce-common/`, `shopping-agent/`, `merchant-agent/`) remain
  upstream-identical on this branch

## Later phases (see `examples/theclub/README.md`)

1. Search + product details over shop.theclub.com.hk Magento GraphQL (read-only)
2. Member identity: tier + Clubpoints balance, personalized points-budget search
3. Cart + checkout handoff to Magento hosted checkout (never autonomous purchase)
4. Points earn / convert advisory flows

## License

Apache 2.0, retained from upstream. See `LICENSE`.
