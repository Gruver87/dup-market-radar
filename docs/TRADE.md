# Trade & Telegram

**Read [`DISCLAIMER.md`](DISCLAIMER.md) before enabling anything that can place orders.**

## Manual KuCoin spot

1. Keys in `.env`.
2. Dashboard **Watchlist** page (`#watchlist`) — **Купить** / **Продать** → preview → confirm.
3. Caps: `max_order_quote_usdt_micros`, symbol meta (min funds / size), sell clamp to available balance.
4. Futures / DEX rows: UI `canTradeRow` hides buttons; product policy is **spot only**.

UI layout: [`DASHBOARD.md`](DASHBOARD.md).

## Auto-trade

Config: `config/default.yaml` → `auto_trade`.

| Rule | Behavior |
|------|----------|
| Default | **OFF** |
| Sectors | `spot_alts` / `memes` (CEX only) / `listings` — futures blocked; DEX memes skipped |
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

## Listings soft risk flags

`auto_trade.listings_soft_risk_flags` (default: `cross_venue_live`) may appear on listing rows without blocking auto-buy when `require_no_risk_flags` is true. Any other flag still blocks.

## Outside-top Telegram stops + fade

If a watched spot symbol drops out of top-N, alerts can still fire **stop-loss** using KuCoin mark (`telegram.outside_top_mark_alerts`, default true).

When the watch previously saw a scored `fade-watch` / `short` (`last_setup` persisted) and mark is in profit ≥ `take_profit_warn_bps`, a **warn** alert fires even outside top-N (`telegram.outside_top_fade_alerts`, default true). Fade is never invented without a remembered setup.

See [`LIVE_MONEY.md`](LIVE_MONEY.md) before disabling dry_run.

## Memes soft risk flags

`auto_trade.memes_soft_risk_flags` (default: `below_vol_floor`) soft-allows that flag on CEX meme rows under `require_no_risk_flags`. Any other flag still blocks. DEX memes remain skipped. CEX meme tops are included in MTF ensure targets.

## Symbol blacklist / whitelist

`auto_trade.symbol_blacklist` / `symbol_whitelist` (empty whitelist = all allowed). Applies to **auto buys only** — stops/sells are never blocked by lists.

## Paper ledger

Dry-run buys/sells append to `data/paper_ledger.json` (integer USDT micros). Ops: `GET /api/trade/paper` (API key when configured). Not a live exchange balance.

## Webhook + WebSocket

- `RADAR_WEBHOOK_URL` or `DISCORD_WEBHOOK_URL` — HTTPS Discord/Slack allowlist; mirrors TG alerts + trade events.
- `GET /ws` — live ticks (`snapshot` / `alert` / `trade` / `ping`). Auth via `?api_key=` when snapshot/trade key required.
