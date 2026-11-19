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

## Consequences

- Maturin still builds `radar_native` from its crate dir (workspace member).
- Later: optional age-gated `price_i` refresh behind explicit flag; MTF/short_window in Rust.
- Market Ping unchanged except faster/fresher Radar snapshots over time.

## Non-goals

- Rewriting Market Ping in Rust
- Merging L1 `dup-protocol` into this workspace
- Enabling futures auto-trade
