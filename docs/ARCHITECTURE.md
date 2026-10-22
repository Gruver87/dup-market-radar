# Architecture

**Pin:** 0.2.85 | Index: [`README.md`](README.md)

## Layers

```text
Dashboard / Telegram / WebSocket (/ws) / Webhook
        |
   FastAPI (radar.api.app)
        |
   Pipeline (ingest -> MTF ensure -> score -> display patch -> snapshot)
        |
   -----+-----
 Python     Rust radar_native (PyO3)  -- score hot path when built
 ingest     score_universe_json
 trade      parity with Python scorers (tests)
 paper      dry-run P&L ledger (USDT micros)
 ops        worldtime / FX / markets (~6m) payload
```

Transport (HTTP/TG) must not reach raw L1-style state: this product is **not** the L1. Persistence is JSON under `data/` (positions, watches, oid ledger) with integrity checks where applicable.

## Self-recovery

`RecoverySupervisor` (daemon) keeps background scans warm, restarts dead auto/Telegram workers under a hourly budget, and emits `network_recovered` after outages ? without enabling auto-trade or clearing fail-closed gates. See [`RECOVERY.md`](RECOVERY.md).

## Ports (industrial base)

- **`SecretManagerPort`** ? `radar.secret_mgmt` (env default; file refused when `deployment_mode=prod`).
- **`MetricsExporterPort`** ? frozen scrape snapshot to Prometheus text (`radar.observability` + `metrics.py`).
- Profiles / evidence: [`PROFILES.md`](PROFILES.md), [`EVIDENCE_MATRIX.md`](EVIDENCE_MATRIX.md), `scripts/radar_gate.py`.

## Dashboard views

Hash-routed sections (`#watchlist` / `#markets` / `#ops` / `#help`). Sector filters apply only to Watchlist. Markets page consumes `/api/ops/bubbles` as a ranked table. See [`DASHBOARD.md`](DASHBOARD.md).

## Snapshot contract (schema_version 2)

Notable fields:

| Field | Meaning |
|-------|---------|
| `ok` | Analysis integrity gate (false on ensure fail / incomplete fill / native prefer + python backend) |
| `mtf_ensure_ok` | Short-window ensure succeeded with >=85% fill on spot/futures/listings/CEX-meme top-N |
| `ingest_ms` / `ensure_ms` / `score_ms` | Phase wall-clock (honesty); display MTF time is charged to `ensure_ms` |
| `venue_ms` | Per-venue ingest wall (parallel max approximates ingest) |
| `short_window_ensure` | Rounds, targets, fill ratios, `ensure_rank_n`, `stage_ms`, `display_*` |
| `score_backend` | `radar_native` or `python_*` / `none` |
| `sectors` | Top-N rows per sector |
| `errors` | Bounded, redacted strings |

Empty / hard-fail path: `empty_snapshot(...)` -> `ok=false`, `mtf_ensure_ok=false`.

## Ingest (parallel)

Venue workers run under `ingest.parallel_workers` (default 6):

| Venue | Bootstrap (pin notes) |
|-------|------------------------|
| KuCoin (primary) | allTickers parallel with contracts, then `/v2/symbols`; whales overlap bootstrap (0.2.73 / 0.2.82) |
| Binance | spot 24hr / fut 24hr / premium / exchangeInfo in parallel (0.2.73) |
| Bybit / MEXC | 3-way parallel each (0.2.77) |
| DexScreener | boosted + batched `/tokens/{a,b,...}`; keyword search soft-skip (0.2.75 / 0.2.78) |
| CoinGecko | stables |

Dex / CoinGecko GETs use `ingest.secondary_http_timeout_ms` (default **2500**, one-shot ? 0.2.81) so secondary hosts cannot stretch ingest past CEX. CEX keeps `http_timeout_ms`.

HTTP client: TLS verify on; allowlist hosts; `httpx.Limits(max_connections=32, max_keepalive_connections=16)` (0.2.82).

## MTF ensure

1. Build universe (group poll: cex / memes / stables).
2. Fill-gate = display-sized **top_n** (24h prelim score). Ensure set = wider **`ensure_rank_n = ensure_cap`** (0.2.79) plus cheap vol+|chg| expand; gate freeze ? no mid-ensure re-rank (0.2.72).
3. Spot and futures waterfalls run **in parallel** (shared kline budget). Per-symbol fan-out: `KLINE_PARALLEL_WORKERS=12` (0.2.80). Venue order KuCoin -> Binance -> Bybit -> MEXC; soft-denylist Binance 400s (0.2.76).
4. Early-exit when spot, futures, listings, and CEX-meme **top_n** fill >= **0.85** -> `mtf_ensure_ok=true`.
5. Score sectors. **Patch-first** from universe onto scored boards. If spot/futures board 15m fill already >= **0.85** (same bar as `mtf_ensure_ok`), skip residual display network (`display_skipped_coverage`, 0.2.85). Otherwise residual miss ensure (spot_alts+futures, cap 8/side) then patch again ? wall time added to `ensure_ms` (0.2.77 / 0.2.78 / 0.2.80). Primary `short_window_ensure` acc is restored after display (no clobber of fill ratios).

**Not** full-universe candles. DEX memes use Dex windows (h24/h1/m5); CEX keyword memes share the spot candle ensure path. Budget: `ingest.max_kline_gets` (default 112).

## Hybrid native

- Crate: `native/radar_native` (Cargo / maturin).
- Config default: `native.prefer` + `fail_closed` true in `config/default.yaml`.
- **Docker / GitHub CI:** `RADAR_NATIVE_*=0` -> documented **python-lab** profile. Do not claim "prod native" from compose alone.

## Money

See `src/radar/money.py`. All monetary and score paths use integers (price 1e8, quote micros 1e6, bps, score centi).

Futures tops skip commodity/FX/index bases (`is_non_crypto_futures_symbol`).

## Related docs

- [`OPS.md`](OPS.md) ? run profiles / performance notes  
- [`DASHBOARD.md`](DASHBOARD.md) ? UI  
- [`TRADE.md`](TRADE.md) ? auto gates  
- [`SECURITY.md`](SECURITY.md) ? exposure  
- [`DISCLAIMER.md`](DISCLAIMER.md) ? liability  
