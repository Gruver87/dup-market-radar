# Architecture blueprint — DUP Market Radar

**Pin:** 0.2.87 · **Kind:** constructor template (columns) · **Not** L1 mesh.

Scan basis: repo tree `src/radar/*`, `native/radar_native`, `config/`, `dashboard/`, `scripts/`, `.github/workflows/` (2026-10-22).

Use this as a **module map / assembly drawing**. Narrative detail stays in [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 0. Title block

| Field | Value |
|-------|--------|
| Product | DUP Market Radar (companion, standalone) |
| Org | DUP Labs |
| Money | integer only (`money.py`: price 1e8, USDT micros 1e6, bps, score centi) |
| Primary venue | KuCoin (spot trade + public books); Binance/Bybit/MEXC ingest |
| Native | `radar_native` PyO3 score hot path (`prefer`+`fail_closed` in `default.yaml`) |
| Persist | `data/` JSON (positions, watches, oid, events) — not L1 RocksDB |
| Evidence | `docs/evidence/runs/*` + `scripts/package_radar_evidence.py` |

---

## 1. Column assembly (left → right = request / scan flow)

```text
+==================+==================+==================+==================+==================+
|   A  SURFACE     |   B  TRANSPORT   |   C  ORCHESTRATE |   D  DOMAIN      |   E  ADAPTERS    |
|   (humans)       |   (edge)         |   (core loop)    |   (logic)        |   (I/O / hot)    |
+==================+==================+==================+==================+==================+
| dashboard/       | FastAPI          | pipeline.        | ingest/          | http_client      |
|   index.html     |   api/app.py     |   run_scan()     |   universe       | http_pool        |
| #watchlist       |   middleware     |   empty_snapshot |   kucoin…mexc    | allowlist HTTPS  |
| #markets         |   auth / RL      | recovery.        |   short_window   | TLS verify ON    |
| #ops #help       |   ws_hub /ws     |   Supervisor     |   mtf_cache      |                  |
|                  |                  | scan_cache       |   sector_poll    | secret_mgmt/     |
| Telegram bot     | notify/webhook   | config.yaml      |   circuit        |   SecretManager  |
|   telegram/*     |                  | + RADAR_* env    |   kline_budget   |                  |
|                  |                  | + light overlay  | score/           | observability/   |
| Manual UI trade  |                  |                  |   engine         |   MetricsExport  |
| Auto ON/OFF      |                  |                  |   spot fut meme  | metrics.py       |
|                  |                  |                  |   list whale     |                  |
|                  |                  |                  |   stables        | native_bridge    |
|                  |                  |                  | classify/ rank/  |   → radar_native |
|                  |                  |                  | trade/           |   score.rs       |
|                  |                  |                  |   auto_engine    |   rank.rs        |
|                  |                  |                  |   kucoin_manual  |   classify.rs    |
|                  |                  |                  |   oid / paper    |                  |
|                  |                  |                  | ops/             | persist.py       |
|                  |                  |                  |   fx bubbles     | audit_log        |
|                  |                  |                  |   world_time     | data/*           |
+==================+==================+==================+==================+==================+
         |                  |                  |                  |                  |
         v                  v                  v                  v                  v
   Browser / TG        HTTP JSON/WS      Snapshot v2         Sector boards      Venues / disk
```

**Assembly rule:** A/B must not call venue HTTP or `data/` writers except through C→D→E. Trade mutations require API key / fail-closed gates (`ok`, `mtf_ensure_ok`, native prefer).

---

## 2. Scan pipeline (horizontal strip — pin 0.2.87)

```text
  force/poll
      |
      v
 +----------+    +-------------+    +----------+    +------------+    +-----------+
 | INGEST   | -> | MTF ENSURE  | -> | SCORE    | -> | DISPLAY    | -> | SNAPSHOT  |
 | universe |    | short_window|    | engine / |    | patch-first|    | schema v2 |
 | venue_ms |    | + mtf_cache |    | native   |    | soft wall  |    | ok / mtf  |
 | stale-OK |    | fill >=0.85 |    | score_ms |    | ->ensure_ms|    | sectors[] |
 +----------+    +-------------+    +----------+    +------------+    +-----------+
   ingest_ms         ensure_ms          ~60ms           (in ensure)      elapsed_ms
```

| Phase | Module anchors | Soft walls / reuse |
|-------|----------------|--------------------|
| Ingest | `ingest/universe.py`, venues, Dex/CG | `secondary_*`, `kucoin_stale_ok_ms`, sector `poll_sec` |
| Ensure | `ingest/short_window.py`, `mtf_cache.py` | `max_kline_gets`, `mtf_reuse_ttl_ms`, gate freeze |
| Score | `score/engine.py` → `native_bridge` | prefer+fail_closed; python = degraded if prefer |
| Display | `pipeline._patch_*`, residual ensure | skip @ board≥0.85; wall base/low |
| Emit | `api` snapshot + WS nudge | redacted `errors[]` |

---

## 3. Package columns (source map)

| Column | Path | Role |
|--------|------|------|
| API | `src/radar/api/` | FastAPI app, WebSocket hub |
| Ingest | `src/radar/ingest/` (17) | Venues, MTF, poll, circuit, budget |
| Score | `src/radar/score/` (9) | Sector scorers + engine |
| Classify / Rank | `classify/`, `rank/` | Labels / top-N helpers |
| Trade | `src/radar/trade/` (10) | Manual, auto, oid, paper, meta |
| Telegram | `src/radar/telegram/` (8) | Bot, watches, alerts, nearest |
| Ops | `src/radar/ops/` | World time, FX, bubbles (~6m) |
| Secrets | `src/radar/secret_mgmt/` | `SecretManagerPort` |
| Observability | `src/radar/observability/` | `MetricsExporterPort` |
| Notify | `src/radar/notify/` | Discord webhook allowlist |
| Core loose | `pipeline`, `money`, `persist`, `recovery`, `http_*`, `auth*`, `rate_limit` | Glue |
| Native | `native/radar_native/src/{lib,score,rank,classify}.rs` | PyO3 hot path |
| UI | `dashboard/index.html` | Hash routes |
| Config | `config/default.yaml`, `config/light.yaml` | Pin + light overlay |
| Gates | `scripts/radar_gate.py`, `verify.ps1`, smokes/soaks | Honesty / evidence |
| CI | `.github/workflows/ci.yml`, `ci-native.yml` | python-lab + maturin parity |

---

## 4. Fail-closed / trust boundaries (constructor notes)

```text
  EXTERNAL HTTPS (allowlist only)
           |
           v
  [AllowlistHttp] --no--→ disable TLS / * CORS / raw secrets in logs
           |
           v
  [pipeline.run_scan] --ok=false--→ auto buys refused; stop-only if bags open
           |
           +-- mtf_ensure_ok=false --→ same
           +-- prefer && python backend --→ ok=false
           |
  [SecretManagerPort] --prod+file--→ ConfigError refuse
  [deployment_mode=prod]            → file secrets refused
```

---

## 5. Profile rack (do not mix claims)

| Slot | How | Native | Claim limit |
|------|-----|--------|-------------|
| lab / default.yaml | local + wheel | prefer+fail_closed | closest honest native |
| python-lab | Docker / `ci.yml` | env `*=0` | full pytest only |
| ci-native | `ci-native.yml` | maturin + parity | **not** full suite under native |
| light | `RADAR_PROFILE=light` | inherits | KuCoin-primary refresh |
| live-money | operator + keys | prefer+fail_closed | see `LIVE_MONEY.md` + soak packs |

---

## 6. Evidence / ops strip

| Artifact | Script |
|----------|--------|
| Static needles | `python scripts/radar_gate.py` |
| Offline / live smoke | `scripts/radar_smoke_lab.py` |
| Warm (stale-OK + MTF cache) | `scripts/radar_warm_smoke_lab.py` |
| Package hashed run | `scripts/package_radar_evidence.py` |
| Short / 48h soak | `radar_soak_lab.py` / `radar_soak_48h.ps1` (**claim only with pack**) |
| Matrix | [`EVIDENCE_MATRIX.md`](EVIDENCE_MATRIX.md) |

---

## 7. Related

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — prose layers / MTF / snapshot fields  
- [`PROFILES.md`](PROFILES.md) · [`OPS.md`](OPS.md) · [`TRADE.md`](TRADE.md) · [`SECURITY.md`](SECURITY.md)  
- Root [`../README.md`](../README.md)

**48h soak not claimed by this blueprint.**
