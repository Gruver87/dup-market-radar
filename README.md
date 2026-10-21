# DUP Market Radar

Monitoring market analyzer for crypto spot, futures, memes, stables, new listings, and large prints.

**Not an auto-trading bot.** Scanner never places orders. Optional **manual** KuCoin Buy/Sell buttons require your click + confirm + API keys in `.env`. Not financial advice.

## What it does

Polls public exchange / DexScreener / CoinGecko endpoints, classifies instruments into sectors, scores each sector with its own interval and rules, and surfaces **top-N (default 10)** candidates per sector in an ops dashboard.

**Primary venue:** [KuCoin](https://www.kucoin.com/) (spot + USDT-M futures). Binance / Bybit / MEXC remain as cross-checks in `config/default.yaml`.

| Sector | Focus |
|--------|--------|
| `spot_alts` | CEX spot alts (momentum + volume surge) |
| `futures` | USDT-M perps (OI + funding confluence → long/short watch) |
| `memes` | DEX pairs (liquidity / vol / age) |
| `stables` | Peg deviation from $1 |
| `listings` | Recently appeared CEX pairs |
| `whales` | Large agg-trade prints (size-based; not labeled Nansen wallets) |

## Quick start

```powershell
cd C:\Users\vovun\Desktop\dup-market-radar
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\scripts\build_native.ps1   # Rust hot path (radar_native) — optional but recommended
.\scripts\run_radar.ps1
```

Open http://127.0.0.1:8787/

```powershell
# Offline gates (no network)
.\.venv\Scripts\python.exe scripts\radar_smoke_lab.py
# One live ingest+score (network)
.\.venv\Scripts\python.exe scripts\radar_smoke_lab.py --live
# Docker (Python scorers; native prefer off via env)
docker compose up --build
```

## Hybrid architecture (like L1 abs_native)

| Layer | Tech | Owns |
|-------|------|------|
| Orchestration / API / trade / UI | Python | FastAPI, KuCoin keys, auto ON/OFF, dashboard |
| Ingest | Python parallel workers | public REST (allowlist + TLS verify) |
| Score + rank hot path | **Rust** `radar_native` (PyO3) | sector scorers, top-N |

Snapshot field `score_backend` is `radar_native` or `python` (honest fallback if native not built).

**Money:** integer fixed-point only (no float for price/size/score). Scales in `radar/money.py` — price `1e8`, USDT micros `1e6`, change **bps**, score **centi**. Ingest keeps exchange **strings** (`radar/ingest/parse.py`); Decimal only for fraction→percent. Trade API: `quote_usdt_micros` / `base_size_i`. Rust `radar_native` ≥0.2; `native.prefer` + `fail_closed: true` — no silent Python rank swap. HTTP timeouts in **ms** (`http_timeout_ms`).

## Config

[`config/default.yaml`](config/default.yaml) — venues (KuCoin first), thresholds, `top_n`, `http_timeout_ms`, ingest retries. Loaded via `radar.config.load_config` with **fail-closed validation** (no futures in `auto_trade.allowed_sectors`, KuCoin required).

## Manual + auto KuCoin trade

1. Copy `.env.example` → `.env`
2. Fill `KUCOIN_API_KEY`, `KUCOIN_API_SECRET`, `KUCOIN_API_PASSPHRASE` (spot trade; IP whitelist recommended)
3. **Manual:** table buttons **Купить** / **Продать** → Превью → Подтвердить
4. **Auto ON/OFF:** header button. Default **OFF**. When ON, engine buys/sells on a cycle for `spot_alts` / `memes` / `listings` only — **never futures**. Limits in `config/default.yaml` → `auto_trade`.
5. Optional paper mode: `auto_trade.dry_run: true` (logs only, no orders)

Manual buttons keep working while auto is ON or OFF.

## Ops endpoints

| Path | Purpose |
|------|---------|
| `/api/live` | liveness (process up) |
| `/api/ready` | readiness (503 until warm snap / if persist broken) |
| `/api/health` | honest ops health (scan / persist / native / circuit) |
| `/api/metrics` | JSON counters |
| `/api/metrics/prometheus` | Prometheus text |
| `/api/snapshot` | sector tops + cache stats |
| `/api/trade/events` | last N trade audit events |
| `/api/telegram/status` | bot / watches / circuit |

CORS is an explicit allow-list (`api.cors_origins`) — never `*`. Trade routes soft-refuse at `api.trade_rate_limit_per_min`.

Optional `RADAR_API_KEY` in `.env`: when set, `POST /api/trade/*` requires header `X-Radar-Api-Key`. Dashboard reads `localStorage.radar_api_key`. Every response includes `X-Request-Id`.

## Telegram bot

1. Create a bot via [@BotFather](https://t.me/BotFather), copy the token
2. Message the bot once, get your chat id (e.g. via `@userinfobot`)
3. In `.env`:

```text
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=123456789
TELEGRAM_ENABLED=1
```

4. Restart Radar — bot starts with the API process (`/api/telegram/status`)

| Command | Purpose |
|---------|---------|
| `/watch BTCUSDT` | spot watch + alerts when fade/stop says sell |
| `/watch ETHUSDT long 2500` | futures LONG watch (warn to close — no auto orders) |
| `/watch SOLUSDT short` | futures SHORT watch |
| `/nearest BTCUSDT buy` | ближайшая точка входа по анализу (±1.5% зона) |
| `/nearest ETHUSDT sell` | ближайшая точка выхода/слива |
| `/buy BTCUSDT 10` | preview; `/buy BTCUSDT 10 confirm` — spot market |
| `/sell ETHUSDT 0.01 confirm` | spot sell |
| `/top spot_alts` · `/positions` · `/alerts` · `/status` | ops |

Alerts fire when analysis flips to fade / stop-loss / opposite futures setup, and include the **nearest analysis exit**. That point is a heuristic zone from `price_i`, **not** the exchange order book.

## Honesty

- Public market data APIs (rate limits apply).
- Whale tab is **notional size**, not smart-money labels.
- Scores are heuristic watchlist ranks, not predictions of profit.
- Secrets never committed (`.env` is gitignored).

## Brand

DUP Labs · companion to industrial L1 work; standalone from the protocol pin and from PSI-MEXC-BOT until a later read-only bridge.
