# Trade & Telegram

**Read [`DISCLAIMER.md`](DISCLAIMER.md) before enabling anything that can place orders.**

## Manual KuCoin spot

1. Keys in `.env`.
2. Dashboard **Купить** / **Продать** → preview → confirm.
3. Caps: `max_order_quote_usdt_micros`, symbol meta (min funds / size), sell clamp to available balance.
4. Futures / DEX rows: UI `canTradeRow` hides buttons; product policy is **spot only**.

## Auto-trade

Config: `config/default.yaml` → `auto_trade`.

| Rule | Behavior |
|------|----------|
| Default | **OFF** |
| Sectors | `spot_alts` / `memes` / `listings` only — futures blocked in config validation |
| Dry run | `dry_run: true` → log only |
| Degraded snap (`ok=false`, `mtf_ensure_ok=false`, native-prefer + python backend) | **No buys** |
| Degraded / empty sectors **with** open positions | **Stop-only** via KuCoin public mark (`get_spot_mark_price_i`) |
| Degraded **without** positions | Cycle refused |
| Persist corrupt | Cannot enable auto |
| Single-flight | Concurrent `run_once` soft-refused |

Fade / setup sells require a healthy analysis row. Hard stop-loss on degrade uses **exchange mark**, not fade labels.

## Telegram

- Watches + alerts (fade / stop / futures flip warn).
- `/buy` `/sell` documented in `/help`.
- Alerts **skip** `ok=false` and stale snapshots (`snapshot_max_age_ms`).
- Futures watches never place futures orders.

## Audit

Trade events → `data/trade_events.jsonl` (gitignored). Messages redacted.
