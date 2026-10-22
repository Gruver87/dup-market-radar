# Architecture

## Layers

```text
Dashboard / Telegram / WebSocket (/ws) / Webhook
        ?
   FastAPI (radar.api.app)
        ?
   Pipeline (ingest ? MTF ensure ? score ? snapshot)
        ?
   -----+-----
 Python     Rust radar_native (PyO3)  ù score hot path when built
 ingest     score_universe_json
 trade      parity with Python scorers (tests)
 paper      dry-run P&L ledger (USDT micros)
 ops        worldtime / FX / markets (~6m) payload
```

Transport (HTTP/TG) must not reach raw L1-style state: this product is **not** the L1. Persistence is JSON under `data/` (positions, watches, oid ledger) with integrity checks where applicable.

## Self-recovery

`RecoverySupervisor` (daemon) keeps background scans warm, restarts dead auto/Telegram workers under a hourly budget, and emits `network_recovered` after outages ù without enabling auto-trade or clearing fail-closed gates. See [`RECOVERY.md`](RECOVERY.md).

## Ports (industrial base)

- **`SecretManagerPort`** ó `radar.secret_mgmt` (env default; file refused when `deployment_mode=prod`).
- **`MetricsExporterPort`** ó frozen scrape snapshot ? Prometheus text (`radar.observability` + `metrics.py`).
- Profiles / evidence: [`PROFILES.md`](PROFILES.md), [`EVIDENCE_MATRIX.md`](EVIDENCE_MATRIX.md), `scripts/radar_gate.py`.

## Dashboard views

Hash-routed sections (`#watchlist` / `#markets` / `#ops` / `#help`). Sector filters apply only to Watchlist. Markets page consumes `/api/ops/bubbles` as a ranked table. See [`DASHBOARD.md`](DASHBOARD.md).

## Snapshot contract (schema_version 2)

Notable fields:

| Field | Meaning |
|-------|---------|
| `ok` | Analysis integrity gate (false on ensure fail / incomplete fill / native prefer + python backend) |
| `mtf_ensure_ok` | Short-window ensure succeeded with ?85% fill on spot/futures/listings/CEX-meme top-N |
| `short_window_ensure` | Rounds, targets, fill ratios (incl. `meme_cex_fill_ratio`) |
| `score_backend` | `radar_native` or `python_*` / `none` |
| `sectors` | Top-N rows per sector |
| `errors` | Bounded, redacted strings |

Empty / hard-fail path: `empty_snapshot(...)` ? `ok=false`, `mtf_ensure_ok=false`.

## MTF ensure

1. Build universe (group poll: cex / memes / stables).
2. Up to **2** ensure rounds: prelim ? `2ùtop_n` scored symbols + listings + **CEX keyword memes**; attach 15m/1h/4h via KuCoin then Binance/Bybit/MEXC.
3. Early-exit when spot, futures, listings, and CEX-meme top-N fill ? **0.85**.
4. After rounds: if still below 0.85 ? `mtf_ensure_ok=false` ? snapshot `ok=false`.

**Not** full-universe candles. DEX memes still use Dex windows (h24/h1/m5); CEX keyword memes share the spot candle ensure path.

## Hybrid native

- Crate: `native/radar_native` (Cargo / maturin).
- Config default: `native.prefer` + `fail_closed` true in `config/default.yaml`.
- **Docker / GitHub CI:** `RADAR_NATIVE_*=0` ? documented **python-lab** profile. Do not claim ùprod nativeù from compose alone.

## Money

See `src/radar/money.py`. All monetary and score paths use integers (price 1e8, quote micros 1e6, bps, score centi).

Futures tops skip commodity/FX/index bases (`is_non_crypto_futures_symbol`).

## Related docs

- [`OPS.md`](OPS.md) ù run profiles  
- [`DASHBOARD.md`](DASHBOARD.md) ù UI  
- [`TRADE.md`](TRADE.md) ù auto gates  
- [`SECURITY.md`](SECURITY.md) ù exposure  
- [`DISCLAIMER.md`](DISCLAIMER.md) ù liability  
