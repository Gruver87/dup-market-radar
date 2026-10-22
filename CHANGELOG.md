# Changelog — DUP Market Radar

## 0.2.71

- DexScreener: parallel token expand + keyword search; soft-skip search when boosted rows ≥24
- Whales: KuCoin-only (Binance aggTrades fan-out removed)
- Process-lifetime `AllowlistHttp` pool (`http_pool.py`) — TLS/cache reuse; close on API shutdown
- Ensure: spot∥futures waterfalls in parallel; atomic thread-safe `budget_consume`
- Package / FastAPI / User-Agent / `__version__` / native pin `0.2.71`
- Soak / 48h: **not** claimed this pin

## 0.2.70

- Ingest speed: Binance OI fan-out parallel (8 workers); whale dual-market cut (spot-only Binance; Bybit whales off — KuCoin primary)
- KuCoin: remove ingest-time MTF attach (ensure+budget owns candles); reuse `contracts/active` in listings (no duplicate GET)
- Observability: `venue_ms`, ensure `stage_ms` on snapshot + Prom (`radar_venue_ms`, `radar_ensure_stage_ms`)
- Fill-check `_top_buckets` prefers `radar_native` (rank parity with final score; Python soft fallback)
- Package / FastAPI / User-Agent / `__version__` / native pin `0.2.70`
- Soak / 48h: **not** claimed this pin

## 0.2.69

- Speed / efficiency wave (P0→P1): phase timers `ingest_ms` / `ensure_ms` + Prometheus gauges (`score_ms`, `kline_gets`, `ensure_rounds`)
- Cheap prelim targets (vol+|chg| / listings / CEX meme keywords) — full sector score only for top-N honesty + optional 2nd ensure
- Ensure waterfall short-circuit at ≥0.85 fill; hard `ingest.max_kline_gets` budget; `mtf_reuse_ttl_ms` stamp skip
- Shared AllowlistHttp across venue workers + ensure (TLS/cache reuse); memes `poll_sec` 45 aligned with refresh
- Startup warm: symbol meta refresh; dedicated `scan_executor_workers` pool for scans
- Package / FastAPI / User-Agent / `__version__` / native pin `0.2.69`
- Soak / 48h: **not** claimed this pin — unit + lab only

## 0.2.68

- Industrial base wave (necessary only): `SecretManagerPort` (env + file refuse in prod), `MetricsExporterPort`, `RADAR_LOG_JSON`
- Evidence: `docs/EVIDENCE_MATRIX.md`, `scripts/package_radar_evidence.py` (hashed run dirs), `docs/PROFILES.md`, `scripts/radar_gate.py`
- Self-recovery (from 0.2.67 WIP): `RecoverySupervisor`, `/api/ops/recovery`, WS reconnect, `docs/RECOVERY.md`
- Health: `secrets` + `deployment_mode`; Prometheus recovery series
- Package / FastAPI / User-Agent / `__version__` / native pin `0.2.68`

## 0.2.66

- Dashboard: crypto bubbles replaced with ranked column table (Markets ~6m)
- Dashboard: multi-page nav like DUP Console (`#watchlist` / `#markets` / `#ops` / `#help`) — no single-page scroll trap
- Dashboard: sector tabs no longer call `scrollIntoView` (stopped jump-to-bottom on click)
- Docs: `docs/README.md` index, `docs/DASHBOARD.md`, refreshed README / ARCHITECTURE / OPS (encoding + verify/soak honesty)
- Package / FastAPI / User-Agent / `__version__` / native pin `0.2.66`

## 0.2.65

- Ops: Minsk (UTC+3) on world clock; BYN (+ RUB) FX via NBRB official (ECB frankfurter has neither)
- Pre-soak gate: `scripts/pre_soak_gate.ps1` (native + pytest + live smoke evidence + short soak)
- Soak lab: prefer/fail_closed native when `radar_native` importable; soft only when missing
- Native crate / pyproject version aligned to app pin `0.2.65`
- Unified verify: `scripts/verify.ps1` profiles Quick / Lab / PreSoak / Soak48h / Native / List

## 0.2.64

- Ensure: CEX keyword memes included in MTF short-window targets + `meme_cex_fill_ratio` honesty
- Telegram: outside-top **fade take-profit** via remembered `last_setup` (`outside_top_fade_alerts`)
- Native compose: `RADAR_SNAPSHOT_REQUIRE_API_KEY=1` by default
- Paper ledger: dry-run P&L in USDT micros (`/api/trade/paper`)
- Webhook mirror: Discord HTTPS allowlist (`RADAR_WEBHOOK_URL` / `DISCORD_WEBHOOK_URL`)
- Auto: `symbol_blacklist` / `symbol_whitelist`
- WebSocket: `/ws` live ticks (snapshot/alert/trade); dashboard soft reconnect nudge
- Soak: `--duration-hours` + `scripts/radar_soak_48h.ps1` (48h claim only when pack says so)

## 0.2.63

- Auto: `memes_soft_risk_flags` (default `below_vol_floor`) — sector soft-flag map
- Scripts: `radar_soak_lab.py` (multi-cycle live gate + `hard_fails`) and `evidence_retention.py`
- Smoke `--evidence` runs retention; docs EVIDENCE / LIVE_MONEY / TRADE updated

## 0.2.62

- Memes: CEX keyword path (KuCoin-first) into memes sector — auto-eligible; DEX still skipped
- Config: include_cex_spot / min_cex_quote_volume_usdt; memes back in llowed_sectors
- CI: ci-native.yml on every PR + main
- Docs: LIVE_MONEY.md checklist before dry_run: false

## 0.2.61

- Ensure: empty target set is N/A (not fake 1.0); all-empty → mtf_ensure_empty_targets / mtf_ensure_ok=false
- Trade: futures policy gate exception → HTTP 503 refuse (never soft-continue to place)
- Bubbles: stale/circuit cache returns ok=false (no silent green)
- Auto: drop dead memes from default allowlist; dry_run: true lab-safe default
- Telegram: start failure surfaced in health + /api/telegram/status; mark-miss counter
- README: honest CI note (python-lab default + optional ci-native)

## 0.2.60

- Auto: `listings_soft_risk_flags` (default `cross_venue_live`) soft-allows listing buys under `require_no_risk_flags`
- Telegram: outside-top stop-loss via KuCoin mark (`outside_top_mark_alerts`)
- API: `snapshot_require_api_key` / `snapshot_require_api_key_non_loopback`; server refuses futures/`USDTM` on `/api/trade/order`
- Smoke: `--live --evidence` → `data/evidence/` pack; `docs/EVIDENCE.md`
- Docker: `Dockerfile.native` + `docker-compose.native.yml`; HEALTHCHECK uses `/api/ready`
- Dashboard: poll due groups + circuit_open in status line

## 0.2.59

- Futures: drop commodity/FX/index USDT-M bases (XAU, USOIL, …); config `sectors.futures.exclude_bases`
- Native crate version aligned to app pin `0.2.59` (was `0.2.0`)
- Docs: public GitHub pin + disclaimer pack (from 0.2.58 release)

## 0.2.58

- Auto: on degraded/empty snap with open positions → **stop-only** via KuCoin mark (buys refused; no silent hold without stop path)
- Trader: `get_spot_mark_price_i` (public level1)
- Dashboard status: `mtf_ensure_ok` + ensure fill ratios
- CI: explicit `python-lab-only` job name / profile step
- Ensure honesty: after rounds, fill <85% on spot/futures/listings top-N sets `mtf_ensure_ok=false` (no silent green); listings included in early-exit fill check
- Metrics: `alerts_skipped_degraded` in JSON + Prometheus
- Fail-closed: auto cycle refuses when `ok=false`, `mtf_ensure_ok=false`, native-prefer + python backend, or empty sectors with open positions
- Telegram alerts skip degraded snapshots (`ok=false`); counter `alerts_skipped_degraded`
- Pipeline: ensure exception → `mtf_ensure_ok=false` and snapshot `ok=false` (visible degrade); `empty_snapshot` sets `mtf_ensure_ok=false`
- Dashboard: `canTradeRow` refuses futures / futures sectors (manual Buy/Sell hidden)
- Docker compose: honest `lab-python` profile comments (no maturin wheel / not native fail-closed)
- Help/i18n: `/buy` `/sell` documented; package / FastAPI / User-Agent / `__version__` `0.2.58`

## 0.2.57

- Speed: HTTP GETs no longer hold client lock during network I/O; parallel kline fan-out (8 workers)
- Ensure: smaller prelim (2×top_n) + adaptive 2nd round only if final top-N fill <85%
- Smoke reports `elapsed_ok` vs 25s budget (evidence; does not fail lab)
- Dashboard: MTF column (15m/1h/4h, listing age, cross-venue live)
- Telegram alerts: surface MTF / listing flags in reasons
- Package / FastAPI / User-Agent / `__version__` `0.2.57`

## 0.2.56

- Listings: require live book (skip ghosts); join spot/futures + symbol-only fallback; `cross_venue_live`
- Ensure short-window MTF targets prelim listing symbols (spot vs futures by `market`)
- Rust `score_listings` parity; smoke reports `listings_mtf` / listing flags
- Package / FastAPI / User-Agent / `__version__` `0.2.56`

## 0.2.55

- Futures ensure: MEXC contract kline fallback (parallel arrays → OHLC) for MEXC-only tops
- Fix: do not stamp KuCoin `…USDTM` symbol_raw onto non-KuCoin futures rows
- Package / FastAPI / User-Agent / `__version__` `0.2.55`

## 0.2.54

- Short-window ensure: Bybit spot klines after Binance (Bybit-only alts e.g. BSBUSDT)
- Skip Binance kline GETs for venue=bybit/mexc (400 spam on stocks/commodities)
- Futures ensure: Binance fapi + Bybit linear kline fallbacks when KuCoin miss → raise fut_mtf
- Shared OHLC MTF apply + quote-turnover vol_vs helper
- Package / FastAPI / User-Agent / `__version__` `0.2.54`

## 0.2.53

- Short-window sample: mix top volume + top |24h change| + BTC/ETH/SOL anchors
- Post-score-target `ensure_universe_short_windows` (prelim top); KuCoin then Binance klines fallback
- Ingest: raise `max_response_bytes` 8→20 MiB — Binance allTickers ~18 MiB (live soft-fail)
- Fix: Binance spot keep `exchangeInfo` status=TRADING only — BREAK ghosts had empty windowed klines → spot_mtf=0
- Package / FastAPI / User-Agent / `__version__` `0.2.53`

## 0.2.52

- Fix: KuCoin futures kline `from`/`to` must be milliseconds (live 400s); spot candles stay seconds
- Fix: normalize candle open time ms→sec so futures closed 1h/4h spans resolve
- whales: join spot short-window bias → `spot_mtf_align` / `spot_mtf_diverge` (no extra GET)
- Live smoke lab reports `spot_mtf` / `fut_mtf` / `meme_mtf` coverage + poll
- Package / FastAPI / User-Agent / `__version__` `0.2.52`

## 0.2.51

- Analysis: memes use DexScreener h24/h1/m5 already on the row (no extra GET); `mtf_diverge_5m`
- listings: join spot short-window fields when present; MTF momentum in score
- Rust parity for memes/listings; Package / FastAPI / User-Agent / `__version__` `0.2.51`

## 0.2.50

- Analysis: KuCoin futures klines (sampled top-N) → `change_bps_{15m,1h,4h}`; shared MTF blend
- futures: side/funding flip uses short-window bias; Rust parity
- Package / FastAPI / User-Agent / `__version__` `0.2.50`

## 0.2.49

- Analysis: same KuCoin 15m candle GET → `vol_vs_median_bps` (last 1h turnover vs prior 1h-window median)
- spot_alts: blend absolute log-volume with own-history surge; flags `vol_surge_1h` / `vol_dry_1h`
- Rust `score_spot_alts` parity for volume blend
- Package / FastAPI / User-Agent / `__version__` `0.2.49`

## 0.2.48

- Ingest: honor `sectors.*.poll_sec` via fetch groups (cex / memes / stables); whales bundled with cex
- Snapshot outer TTL = `min(snapshot_refresh_ms, fastest group)`; `force` refreshes all groups
- Snapshot exposes `poll` / `poll_skipped` / `due_groups`; `SECTOR_META` injects group cadence
- Config: validate `poll_sec` int 5..3600
- Package / FastAPI / User-Agent / `__version__` `0.2.48`

## 0.2.47

- Analysis: same 15min GET lookback ~5h → `change_bps_4h` (16×15m); no extra fan-out
- spot_alts: 24h/4h/1h/15m blend; `mtf_diverge_4h`; setup still short-window biased
- Rust `score_spot_alts` parity for 4h
- Package / FastAPI / User-Agent / `__version__` `0.2.47`

## 0.2.46

- Analysis: one KuCoin 15min candle GET per top-N → `change_bps_15m` + derived 1h
- spot_alts: 24h/1h/15m momentum blend; bias prefers 15m; `mtf_diverge_15m`
- Rust `score_spot_alts` parity for 15m
- Package / FastAPI / User-Agent / `__version__` `0.2.46`

## 0.2.45

- Analysis: KuCoin sampled 1h candles (top-N vol) → `change_bps_1h`; spot_alts 24h/1h momentum blend + `mtf_diverge_1h` / range risk
- Rust `score_spot_alts` parity with 1h blend
- Whales HTTP: use short TTL cache (KuCoin/Bybit/Binance)
- Package / FastAPI / User-Agent / `__version__` `0.2.45`

## 0.2.44

- Hardening closeout: persist errors via `redact_exc`; Binance OI soft-fail logged
- Analysis start: honest `SECTOR_META` (no fake 15m/1h/4h); OI sample 80→24; DexScreener boost expands 40→12
- Package / FastAPI / User-Agent / `__version__` `0.2.44`

## 0.2.43

- Drop `exc_info` on scan/middleware/500 paths (stack before RedactingFormatter)
- `sanitize_trade_request` on API/Telegram trade surface; accounts refuse code/msg only
- FillInfo no longer retains raw KuCoin order blob; symbol map error capped
- Package / FastAPI / User-Agent / `__version__` `0.2.43`

## 0.2.42

- Ingest soft-fail logs (KuCoin/Bybit/Binance/DexScreener): `redact_exc`
- env_load / persist backup / audit_log / score native fail: redacted errors
- Auto sell-fail + telegram trade notify: message soft-caps
- Package / FastAPI / User-Agent / `__version__` `0.2.42`

## 0.2.41

- `sanitize_exchange_surface`: API/Telegram/auto trade expose code/msg/orderId only
- Exchange refuse message: never dump full response dict
- ConfigError: basename-only paths; validation join soft-capped
- Trade events read: `redact_exc` on OSError
- Package / FastAPI / User-Agent / `__version__` `0.2.41`

## 0.2.40

- Score `candidate()`: cap risk_flags/setup/entry_hint/url/chain/metrics; refuse float metrics; https-only url
- Trade order failure: soft-trim exchange blob to code/msg; dashboard 500 uses `detail=`
- Alerts: bound risk_flags join + reasons list; nearest action/setup length caps
- Package / FastAPI / User-Agent / `__version__` `0.2.40`

## 0.2.39

- Enrich: cap symbol/venue/setup string lengths at ingest boundary
- TradeResult/FillInfo: soft-truncate messages (512); stats `inc` key allowlists
- `empty_snapshot` reason ≤256; sector 404 no longer echoes sector_id
- Package / FastAPI / User-Agent / `__version__` `0.2.39`

## 0.2.38

- Money / `keep_num`: refuse numeric strings > 64 chars (Decimal DoS)
- `enrich_list` soft-cap 50k rows
- `MaxHeadersMiddleware`: soft 431 on header count/bytes; config keys validated
- Package / FastAPI / User-Agent / `__version__` `0.2.38`

## 0.2.37

- FX: soft-cap rates book (256); `redact_exc` on frankfurter/bubbles errors
- i18n `t()`: truncate format kwargs (256) / max 32 keys
- Persist: redact corrupt/stat error strings; more FS path keys redacted in API
- Dashboard `esc()` soft-cap 4 KiB
- Package / FastAPI / User-Agent / `__version__` `0.2.37`

## 0.2.36

- HTTP client: scrub/truncate error ring + 429/5xx body snippets
- KuCoin creds / Telegram token: soft length gates (fail-closed empty)
- Watches / auto / native: truncate persist/load errors; access-log method sanitize
- Package / FastAPI / User-Agent / `__version__` `0.2.36`

## 0.2.35

- Ingest universe: redact circuit fail reasons + worker error strings
- Auto-engine / prefs / oid: truncate persist errors; no raw `str(exc)`
- API lifespan/scan/pipeline: `redact_exc` (no token-risk exception dumps)
- `escape_html` soft-cap 16 KiB; Telegram allow-list chats ≤64
- Package / FastAPI / User-Agent / `__version__` `0.2.35`

## 0.2.34

- Telegram: no token-risk `logger.exception` on send/poll; `redact_exc` on user-facing errors
- Trade events: message/reason soft-truncate 512
- API 422: cap error list (32) + loc/msg bounds; 500 path sanitize
- Package / FastAPI / User-Agent / `__version__` `0.2.34`

## 0.2.33

- KuCoin: no `logger.exception` on signed paths; `redact_exc` + truncate trade/fill errors
- `redact_exc` soft-truncate (default 512)
- Audit JSONL: soft-truncate single lines > 64 KiB
- Symbol meta / FX: error + cache base caps; venues list ≤8
- Package / FastAPI / User-Agent / `__version__` `0.2.33`

## 0.2.32

- Circuit: sanitize venue keys, truncate fail reasons (120), soft-cap tracked venues (64)
- Config: allowlist_hosts ≤64; cors/trusted_hosts ≤32
- FX/bubbles: redact+truncate circuit/stale error strings
- Package / FastAPI / User-Agent / `__version__` `0.2.32`

## 0.2.31

- Prometheus: sanitize circuit venue labels; cap open-venue series at 32
- `redact_text` soft-truncate inputs > 64 KiB
- `register_secret` soft cap (64 registered extras)
- Config: `allowlist_hosts` refuse wildcards, `.`/`..`, URL/path, >253 chars
- Package / FastAPI / User-Agent / `__version__` `0.2.31`

## 0.2.30

- Checksum sidecar: size ≤512 + digest length gate before compare
- Ingest `errors` ≤64 (+ truncate marker); `circuit_skipped` ≤32
- Symbol meta soft cap 20k pairs; `X-Permitted-Cross-Domain-Policies: none`
- Dashboard `/` `Cache-Control: no-store`
- Package / FastAPI / User-Agent / `__version__` `0.2.30`

## 0.2.29

- `.env` loader: size / line-count / line-length soft refuse
- Config YAML: refuse files > 1 MiB
- `atomic_write_json` refuse payloads > 8 MiB
- Auth fail soft rate-limit (`auth_fail_rate_limit_per_min`, default 30)
- Dashboard: send `X-Radar-Api-Key` only when length 8..256
- Package / FastAPI / User-Agent / `__version__` `0.2.29`

## 0.2.28

- `read_json_object` soft refuse files > 8 MiB (`integrity=too_large`)
- Refuse `TRACE`/`TRACK` (405); `Cross-Origin-Resource-Policy: same-origin`
- Snapshot `errors` list capped at 32
- Docker: `--limit-concurrency 128` + keep-alive 30; compose publishes `127.0.0.1:8787`
- Package / FastAPI / User-Agent / `__version__` `0.2.28`

## 0.2.27

- AllowlistHttp: **HTTPS-only** scheme refuse; response cache soft cap (256) + TTL prune; error ring (64)
- Telegram WatchBook soft max (`telegram.max_watches`, default 2000) — refuse new when full
- `run_radar.ps1`: `--limit-concurrency 128` + `--timeout-keep-alive 30`
- Package / FastAPI / User-Agent / `__version__` `0.2.27`

## 0.2.26

- Trade events: tail-read last 2 MiB (no full-file `read_text` on large JSONL)
- `MaxUrlLengthMiddleware` — soft 414 when path+query > `max_request_url_bytes` (default 2048)
- Access-log path sanitize (control chars / truncate); strip `Server` response header
- X-Forwarded-For length bound (512) when `RADAR_TRUST_PROXY`
- Telegram `getUpdates` `limit=50` (Telegram max 100)
- PrefsStore soft chat cap (500) with `touched_ms` prune
- Package / FastAPI / User-Agent / `__version__` `0.2.26`

## 0.2.25

- Sanitize `X-Request-Id` (charset + length) — refuse log/header injection
- Unhandled exceptions → redacted `500` + request id (no stack/secrets to client)
- `RequestValidationError` 422 strips `input` values from error payloads
- Snapshot `If-None-Match` length cap (128)
- `/api/trade/events` under trade rate limiter
- Dashboard `fetchTimeout` (AbortController) on ops/trade/snapshot calls
- Package / FastAPI / User-Agent / `__version__` `0.2.25`

## 0.2.24

- OpenAPI /docs /redoc /openapi.json **off by default** (`api.enable_openapi` / `RADAR_ENABLE_OPENAPI=1`)
- Soft rate-limit `/api/live` `/api/ready` `/api/health` (`probe_rate_limit_per_min`, default 300)
- Optional HSTS via `RADAR_HSTS_MAX_AGE` (seconds; unset = no header, localhost-safe)
- `normalize_lang` refuses inputs longer than 32 chars
- Package / FastAPI / User-Agent / `__version__` `0.2.24`

## 0.2.23

- `Cache-Control: no-store` for `/api/ops`, `/api/snapshot`, `/api/sector`
- FX frankfurter soft circuit (3 fails → 60s; serve stale rates when possible)
- Bubbles `?force=true` shares force-scan rate limiter
- FX query length cap; metrics endpoints soft rate-limit (`metrics_rate_limit_per_min`)
- Package / FastAPI / User-Agent / `__version__` `0.2.23`

## 0.2.22

- Bubbles: HTTPS CoinGecko-CDN-only image URLs; drop javascript/off-host
- Bubbles: CoinGecko soft circuit (3 fails → 60s open, serve stale)
- `/api/sector/*` under snapshot rate limit (same as `/api/snapshot`)
- `/api/ops/worldtime` lang via `normalize_lang`
- Package / FastAPI / User-Agent / `__version__` `0.2.22`

## 0.2.21

- Crypto bubbles: top-100 by market cap + ~6m change (CoinGecko `200d`, labeled honestly)
- `GET /api/ops/bubbles` — live poll with cache TTL (default 30s), soft-stale on upstream fail
- Dashboard bubbles panel (size ∝ mcap, color ∝ 6m %, live refresh)
- Integer fields: `price_i`, `market_cap_i`, `change_6m_bps`
- Package / FastAPI / User-Agent / `__version__` `0.2.21`

## 0.2.20

- EN/RU language: Telegram `/lang en|ru` (persisted prefs) + dashboard toggle
- World clock: `/time` + `GET /api/ops/worldtime` (fixed UTC offsets)
- Fiat FX: `/fx AMOUNT FROM TO` + `GET /api/ops/fx` via ECB frankfurter (integer micros)
- Allowlist host `api.frankfurter.app`
- `/api/ops/*` soft rate limit (`ops_rate_limit_per_min`, default 60)
- Package / FastAPI / User-Agent / `__version__` `0.2.20`

## 0.2.19

- RateLimiter: key normalize/truncate + `max_keys` cap (memory DoS soft-refuse)
- API key: min 8 / max 256 chars; oversized `X-Radar-Api-Key` refused before digest
- `/api/snapshot` soft rate limit (`api.snapshot_rate_limit_per_min`, default 120)
- KuCoin signed path: must start `/api/`, refuse `..` / scheme / oversize
- Package / FastAPI / User-Agent / `__version__` `0.2.19`

## 0.2.18

- Telegram client: method charset allowlist + 1 MiB response cap via `read_capped_json`
- Public snapshot/sector/health: redact auto `positions` book (`positions_redacted`)
- GET `/api/trade/auto`, `/api/trade/status`, `/api/telegram/status` behind API-key gate
- Dashboard: `enrichTradeDetail` pulls full position book when `radar_api_key` is set
- Package / FastAPI / User-Agent / `__version__` `0.2.18`

## 0.2.17

- Shared `read_capped_json` — signed KuCoin (1 MiB) + symbol-meta (8 MiB) response caps
- Trade JSON POSTs: require `Content-Length` (411) — no chunked bypass of body cap
- `/api/trade/events` behind API-key gate (same as trade mutations)
- Package / FastAPI / User-Agent / `__version__` `0.2.17`

## 0.2.16

- `validate_currency` on KuCoin balance lookup — refuse path/query injection
- HTTP connect timeout: signed KuCoin / symbol-meta 5s; allowlist client `min(5, max(1, read/2))`
- Public JSON: redact filesystem `path` / `ledger_path` / `data_dir` (snapshot, sector, health, trade/tg status)
- Lifespan: refuse start when `RADAR_REQUIRE_API_KEY` set but `RADAR_API_KEY` missing (pytest soft-warn)
- Package / FastAPI / User-Agent `0.2.16`

## 0.2.15

- `validate_order_id` — refuse path/query injection on KuCoin fill reconcile GET
- Whale / token-expand ingest soft-fails logged (binance, bybit, kucoin, dexscreener)
- HTTPException handler: scrub detail + always set `X-Request-Id`
- Package / FastAPI / User-Agent `0.2.15`

## 0.2.14

- Ingest HTTP: `max_response_bytes` (default 8 MiB) — refuse oversized venue JSON
- Trade POST bodies: require `Content-Type: application/json` (415 otherwise)
- Prometheus: `radar_http_response_too_large_total`
- Package / FastAPI / User-Agent `0.2.14`

## 0.2.13

- Atomic JSON persist: `fsync` before rename (crash-safe OID / positions / watches)
- `/api/ready` + `/api/health`: `symbol_meta_base_url_refused` when meta base allowlist fails
- Package / FastAPI / User-Agent `0.2.13`

## 0.2.12

- Symbol meta: KuCoin base allowlist + `follow_redirects=False`; int fetch epoch; status `base_url_ok`
- Rate-limit keys: peer IP only; `RADAR_TRUST_PROXY=1` required to honor `X-Forwarded-For`
- Sensitive `/api/*` responses: `Cache-Control: no-store`
- Native load errors redacted before health/status echo
- Package / FastAPI / User-Agent `0.2.12`

## 0.2.11

- Persist/API timestamps as integer epoch: auto-trade `at`/`updated_at`, watches `updated_at`, snapshot `generated_at`
- `elapsed_sec` integer (floor ms/1000); TG stale check accepts int-sec or legacy float-sec
- Package / FastAPI / User-Agent `0.2.11`

## 0.2.10

- KuCoin signed httpx: `follow_redirects=False` (no off-origin credential hop)
- OID ledger: integer epoch-second timestamps (legacy float `at` still readable)
- `api.max_request_body_bytes` Content-Length cap (default 64 KiB → 413)
- Docker image ENV `RADAR_REQUIRE_API_KEY=1`
- Package / FastAPI / User-Agent `0.2.10`

## 0.2.9

- `api.trusted_hosts` TrustedHostMiddleware (no `*`); pytest adds `testserver` only under pytest
- Telegram httpx: explicit `follow_redirects=False` + TLS verify
- HTTP User-Agent `DUP-Market-Radar/0.2.9`
- Package / FastAPI version `0.2.9`

## 0.2.8

- `RADAR_REQUIRE_API_KEY=1` fail-closed: trade mutations 503 if `RADAR_API_KEY` unset
- `/api/ready` + `/api/health`: `api_key_required_but_unset`; health exposes gate flags
- Prometheus: `radar_cache_scan_errors_total`
- Docker compose defaults `RADAR_REQUIRE_API_KEY=1`
- Package / FastAPI version `0.2.8`

## 0.2.7

- Fill reconcile: `resolve_base_asset` logs map failures (no silent `pass`)
- KuCoin / DexScreener ingest soft-fails logged (futures tickers, listings, boosted/search)
- Audit rotate / backup prune unlink errors logged at debug
- Snapshot cache: log + `scan_errors` counter on scan failure (still re-raises)
- Telegram: warn on non-integer chat ids in allow-list env
- Package / FastAPI version `0.2.7`

## 0.2.6

- KuCoin `KUCOIN_API_BASE` HTTPS host allowlist (SSRF fail-closed); origin-only
- Telegram per-chat command rate-limit (`cmd_rate_limit_per_min`) + metric
- `/api/ready` + `/api/health`: `kucoin_base_url_refused` when base allowlist fails
- Package / FastAPI version `0.2.6`

## 0.2.5

- Telegram alert counters (`alerts_sent` / `skipped_stale` / `skipped_empty`) in status + Prometheus
- Buy funds floored to KuCoin `quoteIncrement` before minNotional gate
- Post-buy TG auto-watch failures surfaced to chat + `watch_add_fail` metric (no silent `pass`)
- Secret redaction: Telegram token / KuCoin keys / `RADAR_API_KEY` scrubbed from logs, audit JSONL, circuit reasons, and exception chains
- Outbound scrub: snapshot `errors`, HTTPException details, Telegram replies, KuCoin trade messages — no secret echo to clients
- FastAPI app version aligned to `0.2.5`
- OID ledger: corrupt/checksum mismatch → `persist_ok=false`, claim refuse (fail-closed)
- `/api/ready` + `/api/health`: flag `oid_ledger_persist_degraded` / `telegram_watches_persist_degraded`
- API-key auth counters (`auth_ok` / `auth_fail`) in health + Prometheus
- HTML escape: Telegram alerts/commands + dashboard `innerHTML` (XSS); https-only row links
- entry_price enrich failures logged (no silent `pass`)
- Security headers (CSP / X-Frame-Options / nosniff / Referrer-Policy)
- Fat-finger caps: `api.max_order_quote_usdt_micros` / `max_order_base_size_i` on place
- Soft rate-limit `?force=true` scans (`force_scan_rate_limit_per_min`)
- Strict trade `symbol` / `client_oid` validation (charset+length); TG text >256 refused; sector_id sanitized

## 0.2.4

- Telegram bot: watches, fade/stop/futures-flip alerts, `/nearest` analysis zones
- Spot `/buy` `/sell` from Telegram (confirm); auto-watch after live buy
- Futures = watch/warn only (never auto-orders)
- Auto-trade → Telegram notify + auto spot watch; `data/trade_events.jsonl` audit
- Dashboard / snapshot / health: telegram status
- Buy minNotional = max(minFunds, quoteMinSize); Telegram API soft circuit
- Trade events carry `request_id` (X-Request-Id context)
- Telegram watch TTL prune + sync auto bags on start; metrics gauges
- `GET /api/trade/events` — last N audit lines
- Unwatch spot on live sell; skip TG alerts if snapshot stale
- Smoke lab: offline telegram nearest/alert gates

## 0.2.3

- Docker image + compose (native prefer off via env)
- `X-Request-Id` middleware; optional `RADAR_API_KEY` on trade POSTs
- Positions JSON SHA-256 sidecar; integrity in auto status
- `/api/live` + `/api/ready` (k8s-style); CI workflow
- Positions rotating backups (`data/backups/`); snapshot `ETag` / 304
- KuCoin fill reconcile → `base_size_i` from `dealSize` (net base fee); size_basis honesty
- Rotating `auto_trade_log.jsonl` audit (`audit_log_max_bytes` / `audit_log_keep`)
- Manual `/api/trade/order` returns `fill` + `order_id` when `reconcile=true`
- Dashboard: open positions bag (`size_basis` / `order_id`); ruff lint in CI
- `clientOid` ledger — duplicate live submit soft-refused (persisted)
- Sell clamp to KuCoin trade-account `available`; trade counters in metrics
- Auto cycle single-flight; lifespan joins auto thread on shutdown
- Trade 429 includes `Retry-After`; dashboard stable `client_oid` per modal
- KuCoin symbol meta gate (`minFunds` / `baseMinSize` / increment floor)
- `POST /api/trade/reconcile` + auto refresh unreconciled bags before sells

## 0.2.2

- Metrics JSON/Prometheus; CORS allow-list; trade rate-limit; warm scan

## 0.2.1

- Single-flight snapshot cache; venue circuit breaker; smoke lab

## 0.2.0

- Integer fixed-point money end-to-end; `radar_native` 0.2 int scorers
- Config validation; HTTP retry; KuCoin-first dedupe
