# Trade & Telegram

**Read [`DISCLAIMER.md`](DISCLAIMER.md) before enabling anything that can place orders.**

## Manual KuCoin spot

1. Keys in `.env`.
2. Dashboard **Watchlist** page (`#watchlist`) — **Купить** / **Продать** → preview → confirm.
3. Caps: `max_order_quote_usdt_micros`, symbol meta (min funds / size), sell clamp to available balance.
4. Futures / DEX rows: UI `canTradeRow` hides buttons; product policy is **spot only**.

UI layout: [`DASHBOARD.md`](DASHBOARD.md).

## Auto-execute (autonomous mode)

Config: `config/default.yaml` → `auto_trade`.  
Runtime toggle = **auto_execute** (`enabled` in API / dashboard «Автоисполнение»). **OFF by default** — never silently arms live trading.

| Rule | Behavior |
|------|----------|
| Default | **OFF** (`enabled_default: false`) |
| Armed | `enabled` + keys + `dry_run: false` + persist OK + kill-switch clear → live KuCoin spot market orders **without** browser confirm |
| Dry run | `dry_run: true` → cycle may log actions; status `armed=false` (no live auto orders) |
| Manual buttons | Always keep Preview → Confirm (independent of auto-execute) |
| Sectors | `spot_alts` / `memes` (CEX only) / `listings` — futures blocked; DEX memes skipped |
| Buys | `buy_setups` (default `buy-watch`, `new-listing-watch`; live-money also `long`) + min score + risk flags; sizing: `equal_sleeves` (1 open/sector) or live-money `shared_pool` (one free-USDT pool, any sector up to `max_open_positions`); `exec_venues` defaults to KuCoin |
| Sells | `sell_setups` (default `fade-watch`) **or** `stop_loss_bps` from entry |
| Quote size | **`sizing_mode: equal_sleeves`** (default): free USDT split equally across `allowed_sectors`; buy = that sleeve’s cash; sell credits the same sleeve. Legacy: `fixed_quote` + `quote_usdt_micros_per_buy`. Hard cap: `api.max_order_quote_usdt_micros` |
| Open bags | `max_open_positions`; equal_sleeves also **one open per sector** |
| Daily rails | `max_daily_buys`, `max_daily_notional_usdt_micros` (UTC day; `0` = unlimited) |
| Cooldown | `signal_cooldown_sec` same symbol+side (hard stop-loss exempt) |
| Kill-switch | `POST /api/trade/auto/kill` → immediate OFF + latch; `clear-kill` then enable again |
| Degraded snap (`ok=false`, `mtf_ensure_ok=false`, native-prefer + python backend) | **No buys** |
| Degraded / empty sectors **with** open positions | **Stop-only** via KuCoin public mark (`get_spot_mark_price_i`) |
| Degraded **without** positions | Cycle refused |
| Persist corrupt | Cannot enable auto |
| Single-flight | Concurrent `run_once` soft-refused |
| Fill reconcile | `fill_reconcile` (default true) sizes bags from KuCoin fills |

Fade / setup sells require a healthy analysis row. Hard stop-loss on degrade uses **exchange mark**, not fade labels.

### Enable live auto-execute (operator)

1. Read [`DISCLAIMER.md`](DISCLAIMER.md) + [`LIVE_MONEY.md`](LIVE_MONEY.md).
2. Keys in `.env`; prefer `RADAR_PROFILE=live-money` (`dry_run: false` overlay) — lab default stays `dry_run: true`.
3. Dashboard: confirm Watchlist `ok` / `mtf_ensure_ok`, then **Автоисполнение: ON**.
4. Status must show **ARMED** (not only ON + `dry_run=true`).
5. Emergency: **СТОП** → kill-switch. Clear latch before re-enable.

## Telegram

- Watches + alerts (fade / stop / futures flip warn).
- `/buy` `/sell` documented in `/help`.
- Alerts **skip** `ok=false` and stale snapshots (`snapshot_max_age_ms`).
- Futures watches never place futures orders.

## Audit

Trade events → `data/trade_events.jsonl` (gitignored). Messages redacted.

## Listings soft risk flags

`auto_trade.listings_soft_risk_flags` (default: `cross_venue_live`) may appear on listing rows without blocking auto-buy when `require_no_risk_flags` is true. Any other flag still blocks.

`auto_trade.spot_alts_soft_risk_flags` (live-money overlay) soft-allows common tape flags (`wide_range_24h`, `mtf_diverge_*`, `wide_spread_*` prefix). Unknown flags still block.

Open bags are monitored every cycle via KuCoin **mark** (even when the symbol leaves the top-N board).

Sell priority: `stop_loss` → `max_hold_sec` → **trailing** → hard `take_profit` → **ROI ladder** → `sell_setups` (fade-watch, skipped when `|move| < fade_min_abs_bps`).

Live-money defaults (practice pin — **no profit guarantee**): stop 10%, TP 8%, trail arm +3% / pull −2%, fade min 1%, ROI 4%→2%→1%→0 by hold time, max hold 4h, cycle 45s, `min_score` 45, no `long` setup.

Also: daily / per-cycle buy&sell caps **0=unlimited** (measurement); anti-chase if `change_bps_1h ≥ 500`; PnL **net of** `fee_bps_per_side` (10); after **3** losing sells pause new buys 1h (exits continue). `max_open_positions` still caps concurrent bags.

## Outside-top Telegram stops + fade

If a watched spot symbol drops out of top-N, alerts can still fire **stop-loss** using KuCoin mark (`telegram.outside_top_mark_alerts`, default true).

When the watch previously saw a scored `fade-watch` / `short` (`last_setup` persisted) and mark is in profit ≥ `take_profit_warn_bps`, a **warn** alert fires even outside top-N (`telegram.outside_top_fade_alerts`, default true). Fade is never invented without a remembered setup.

See [`LIVE_MONEY.md`](LIVE_MONEY.md) before disabling dry_run.

## Memes soft risk flags

`auto_trade.memes_soft_risk_flags` (default: `below_vol_floor`) soft-allows that flag on CEX meme rows under `require_no_risk_flags`. Any other flag still blocks. DEX memes remain skipped. CEX meme tops are included in MTF ensure targets.

## Symbol blacklist / whitelist

`auto_trade.symbol_blacklist` / `symbol_whitelist` (empty whitelist = all allowed). Applies to **auto buys only** — stops/sells are never blocked by lists.

Live-money blacklists **BTCUSDT** / **ETHUSDT** (majors — long-horizon only; not for the short radar cycle).

## One-tap sell (Trades → Open)

Open-position **Sell** places a KuCoin spot market sell immediately (`confirm=true`, full `base_size_i`) — no preview dialog. Requires `RADAR_API_KEY` in the dashboard. Accidental taps are live orders.

## Find coin (Trades → Найти)

`GET /api/trade/lookup?symbol=DOT` resolves spot USDT pair + KuCoin mark + available balance + open bag. Dashboard **Найти** panel buys/sells with one tap (no preview dialog). Default buy size = `quote_usdt_micros_per_buy`.

## Daily report (Trades → Отчёт)

`GET /api/trade/daily_report?days=30` — per UTC day: invested (buy sum), sold, realized P/L (on sell day), W/L. Built from live `trade_events.jsonl` only.

## Paper ledger

Dry-run buys/sells append to `data/paper_ledger.json` (integer USDT micros). Ops: `GET /api/trade/paper` (API key when configured). Not a live exchange balance.

## Webhook + WebSocket

- `RADAR_WEBHOOK_URL` or `DISCORD_WEBHOOK_URL` — HTTPS Discord/Slack allowlist; mirrors TG alerts + trade events.
- `GET /ws` — live ticks (`snapshot` / `alert` / `trade` / `ping`). Auth via `?api_key=` when snapshot/trade key required.
