# ADR-001 — Rust engine layers (core / ingest / native)

- **Status:** Accepted
- **Date:** 2026-11-19
- **Product:** dup-market-radar (not L1 mesh, not Market Ping rewrite)

## Context

Market Ping is a thin Telegram consumer. Scale and latency live in Radar:
ingest → normalize → score → snapshot. Scoring already has a Rust hot path
(`radar_native` / PyO3). Full Python rewrite of the bot would not fix venue I/O
or CPU-bound MTF. Competitors with strong engines keep a **compiled core** and
a Python control plane.

## Decision

Adopt a three-layer Rust topology under `native/`:

| Crate | Role | pyo3? |
|-------|------|-------|
| `radar_core` | Shared types: `Tick`, integer money (`1e8` / USDT micros), parse helpers | **no** |
| `radar_ingest` | Venue WebSocket → normalize → `Tick` stream; binaries per venue family | **no** |
| `radar_native` | Existing score/rank PyO3 binding; Python orchestration stays here | **yes** |

Rules:

1. **Core has zero pyo3** — Rust-only consumers and future ingest daemons can depend on it.
2. **Ingest does not score** — only produce normalized ticks / bars.
3. **Python owns** config, HTTP snapshot API, Telegram, trade gates; Rust owns hot paths.
4. **Futures auto remains forbidden** — ingest may watch funding/OI later; no order path in ingest crates.
5. **Fail-closed honesty** — missing native/ingest never invents prices; Python scorers remain fallback when prefer allows.

## First slice (0.2.90)

- Workspace `native/Cargo.toml`
- `radar_core` + `radar_ingest` (Binance combined trade stream → NDJSON ticks)
- CLI smoke: `radar-ingest-binance --seconds N`
- Docs + `scripts/check_ingest.ps1`
- Does **not** replace HTTP poll pipeline yet — additive path

## Second slice (0.2.91)

- `radar-ingest-bybit` (Bybit v5 spot `publicTrade`)
- Forever mode `--seconds 0` for sidecar
- Python `tick_ring` + opt-in `ingest.native_ws` → snapshot/`/api/native/ws` overlay
- Still **does not** replace HTTP poll scored prices

## Third slice (0.2.92)

- `radar-ingest-kucoin` (bullet-public + `/market/match`)
- Snapshot row annotation: `ws_price_i` / `ws_age_ms` when fresh (`annotate_rows`)
- Robust release-binary discovery (`CARGO_TARGET_DIR` / sandbox target)
- Still **does not** replace scored `price_i`

## Fourth slice (0.2.94)

- `native/.cargo/config.toml` pins `target-dir = target` for sidecar binary discovery
- `scripts/build_ingest.ps1` — release ingest bins
- Explicit `ingest.native_ws.apply_ws_to_price_i` → universe `price_i` before score
- `radar_core::Bar` stub for future MTF

## Fifth slice (0.2.95)

- `apply_ws_to_price_i: true` by default (age-gated; `price_i_http` kept)
- `BarAggregator` + `change_bps` in `radar_core`; `radar-ingest-bars` smoke binary
- HTTP kline short_window still SoT for MTF until bars are merged

## Sixth slice (0.2.96)

- Python bar fold in `tick_ring` + `stamp_ws_bar_metrics` → universe
- Spot score: soft 15m blend proxy from `change_bps_ws_1m` with `mtf_proxy_ws_1m` risk
- Soft `change_bps_5m` fill only when HTTP empty (labeled proxy)

## Seventh slice (0.2.97)

- Multi-interval sidecar bars (1m+5m); futures score WS proxy parity
- `verify_native.ps1` one-shot gate (no soak)

## Eighth slice (0.2.98)

- 15m WS bars; dense promote → skip HTTP short-window kline for those symbols
- Labeled sources; 1h/4h still HTTP when ensure runs for other symbols

## Ninth slice (0.2.99)

- Selective HTTP 1h/4h for WS-promoted rows (`htf_backfill_when_ws`) without overwriting WS 15m
- Honesty: keep `mtf_source=native_ws`, set `htf_source=http_kline` when higher TF filled

## Tenth slice (0.2.100)

- WS 1h bars (3600s); dense promote into `change_bps_1h` (labeled); 4h remains HTTP backfill
- Config/validation interval ceiling raised to 3600

## Eleventh slice (0.2.101)

- Concurrent multi-venue WS (`ingest.native_ws.venues`) — one ingest proc per venue, shared ring/bars
- Default `[binance, bybit]`; KuCoin still available in the list (cap 3)

## Twelfth slice (0.2.102)

- WS 4h bars (14400s); dense promote into `change_bps_4h` (labeled)
- Interval list ceiling 5; config each 5..14400

## Thirteenth slice (0.2.103)

- Per-venue symbol lists (`ingest.native_ws.venue_symbols`) for concurrent multi-venue feeds
- Union symbols for shared bars; snapshot exposes `symbols_by_venue`

## Fourteenth slice (0.2.104)

- `radar-ingest-mexc` — MEXC spot `aggre.deals` protobuf (minimal proto + vendored protoc)
- Opt-in via `venues` / `venue: mexc` (default still binance+bybit)

## Fifteenth slice (0.2.105)

- Denser default venues `[binance, bybit, mexc]`; list cap 4

## Sixteenth slice (0.2.106)

- Dynamic WS symbols from scored sectors (throttled restart)
- Primary-venue price/bar policy; ring_drops + stale_by_venue honesty fields

## Consequences

- Maturin still builds `radar_native` from its crate dir (workspace member).
- Later: additional venue adapters / soak evidence (not claimed here).
- Market Ping unchanged except faster/fresher Radar snapshots over time.

## Non-goals

- Rewriting Market Ping in Rust
- Merging L1 `dup-protocol` into this workspace
- Enabling futures auto-trade
