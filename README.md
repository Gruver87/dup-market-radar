# DUP Market Radar

**Version:** 0.2.78 · **Org:** [DUP Labs](https://github.com/Gruver87) · **Author:** Uladzimir Dabranski

Hybrid **Python + Rust** market watchlist for crypto spot, USDT-M futures (watch-only), memes, stables, listings, and large prints — with optional **KuCoin spot** manual orders and **opt-in auto-trade** (spot_alts / CEX memes / listings; **never futures**; DEX memes watchlist-only).

> [!IMPORTANT]
> **Not financial advice. Not a broker. Not investment management.**
> Heuristic rankings ? predictions of profit. You alone decide and execute risk.
> **The author and DUP Labs accept no liability** for losses, missed stops, API/outage errors, or misuse.
> Full text: [`docs/DISCLAIMER.md`](docs/DISCLAIMER.md).

| Layer | Role |
|-------|------|
| Watchlist / MTF / dashboard / Telegram alerts | Primary product |
| Manual KuCoin Buy/Sell | Opt-in; confirm required |
| Auto ON/OFF | Default **OFF**; fail-closed on degraded snapshots; stop-only path if bags open |

Companion to industrial L1 work ([`dup-protocol`](https://github.com/Gruver87/dup-protocol) / [`dup-protocol-experimental`](https://github.com/Gruver87/dup-protocol-experimental)) — **standalone**; not the L1 pin and not a “mainnet trading stack.”

**Repo:** https://github.com/Gruver87/dup-market-radar

---

## Features (honest)

- **Sectors:** `spot_alts`, `futures` (+ long/short views), `memes`, `stables`, `listings`, `whales` (size proxy — **not** smart-money labels).
- **MTF short windows** (15m / 1h / 4h) for sampled **top-N** (not full universe); KuCoin > Binance > Bybit > MEXC fallbacks.
- **Listings:** live book join; ghost listings skipped; `cross_venue_live` flags.
- **Integer money** only (price `1e8`, USDT micros `1e6`, bps, score centi) — no float on money paths.
- **Security posture:** HTTPS allowlist, TLS verify on, CORS deny `*`, API key gate for trade mutations, secret redaction in logs.
- **Hybrid score:** Rust `radar_native` (PyO3) when built; Docker/CI default to **python-lab** profile (native prefer off) — see honesty below.
- **Auto fail-closed:** refuses buys on `ok=false` / `mtf_ensure_ok=false` / native-prefer + python backend; with open positions runs **stop-only** via KuCoin mark.
- **Dashboard pages** (like DUP Console): Watchlist · Markets ~6m table · Ops (clock/FX) · Help — hash routes `#watchlist` / `#markets` / `#ops` / `#help`.
- **Live ticks:** WebSocket `/ws` nudges refresh (reconnect with backoff); HTTP poll remains source of truth.
- **Self-recovery:** background scan + worker watchdog after network/thread death — see [`docs/RECOVERY.md`](docs/RECOVERY.md) (does **not** auto-enable trade).
- **SecretManagerPort** + prod refuse file secrets; **MetricsExporterPort**; optional `RADAR_LOG_JSON=1`.
- **Evidence matrix** + hashed `package_radar_evidence.py`; static `radar_gate.py` needles.

---

## Quick start (Windows)

```powershell
cd dup-market-radar
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Recommended for prod-like scoring:
.\scripts\build_native.ps1
copy .env.example .env   # fill keys only if you trade
.\scripts\run_radar.ps1
```

Open [http://127.0.0.1:8787/](http://127.0.0.1:8787/) — bind is loopback by default in Docker compose.

```powershell
# Unified operator gates:
.\scripts\verify.ps1 -Profile List      # list profiles
.\scripts\verify.ps1 -Profile Quick     # pytest + smoke offline
.\scripts\verify.ps1 -Profile Lab       # + live smoke --evidence (default)
.\scripts\verify.ps1 -Profile PreSoak   # + native + short soak (not 48h)
.\scripts\verify.ps1 -Profile Soak48h   # PreSoak then 48h (claim only from pack)
```

### Docker (lab-python)

```powershell
docker compose up --build
```

Compose sets `RADAR_NATIVE_PREFER=0` / `FAIL_CLOSED=0` on purpose (no maturin wheel in the image). That is **not** the same as `config/default.yaml` native fail-closed. See [`docs/OPS.md`](docs/OPS.md).

---

## Documentation

Full index: [`docs/README.md`](docs/README.md).

| Doc | Contents |
|-----|----------|
| [`docs/DISCLAIMER.md`](docs/DISCLAIMER.md) | **No liability** — read before enabling trade |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Layers, MTF ensure, hybrid native |
| [`docs/DASHBOARD.md`](docs/DASHBOARD.md) | Multi-page UI, Markets table, hash nav |
| [`docs/RECOVERY.md`](docs/RECOVERY.md) | Self-heal after outages / dead workers |
| [`docs/PROFILES.md`](docs/PROFILES.md) | Named profiles lab / native / live-money |
| [`docs/EVIDENCE_MATRIX.md`](docs/EVIDENCE_MATRIX.md) | Claim > pack honesty matrix |
| [`docs/OPS.md`](docs/OPS.md) | Run, Docker, verify profiles, metrics |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Keys, CORS, rate limits, exposure |
| [`docs/TRADE.md`](docs/TRADE.md) | Manual / auto / Telegram trade gates |
| [`docs/LIVE_MONEY.md`](docs/LIVE_MONEY.md) | Checklist before `dry_run: false` |
| [`docs/EVIDENCE.md`](docs/EVIDENCE.md) | Smoke / pre-soak / soak packs |
| [`docs/BRAND.md`](docs/BRAND.md) | DUP Labs naming & GitHub URLs |
| [`CHANGELOG.md`](CHANGELOG.md) | Release notes |

---

## Config

[`config/default.yaml`](config/default.yaml) — venues (KuCoin first), thresholds, `top_n`, ingest, `auto_trade`, Telegram. Validated fail-closed (no futures in `auto_trade.allowed_sectors`).

Copy [`.env.example`](.env.example) > `.env`. **Never commit `.env`.**

---

## Ops endpoints

| Path | Purpose |
|------|---------|
| `GET /api/live` | Liveness |
| `GET /api/ready` | Readiness (503 if cold / persist broken) |
| `GET /api/health` | Honest ops health |
| `GET /api/metrics` · `/api/metrics/prometheus` | Counters |
| `GET /api/snapshot` | Sector tops + ensure / poll / errors |
| `GET /api/ops/bubbles` | Top-100 ~6m markets payload (table UI) |
| `GET /api/ops/worldtime` | Fixed-offset world clock (incl. Minsk) |
| `GET /api/ops/fx` | FX convert (ECB + NBRB for BYN/RUB) |
| `GET /api/ops/recovery` | Self-recovery supervisor status |
| `POST /api/trade/*` | Manual / auto (API key when required) |
| `GET /api/telegram/status` | Bot / watches / circuit |
| `WS /ws` | Live nudge (snapshot / alert / trade) |

---

## Telegram (optional)

Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (or allow-list), `TELEGRAM_ENABLED=1`. Commands include `/watch`, `/nearest`, `/buy`, `/sell`, `/top`, `/positions`, `/status`, `/time`, `/fx`, `/lang`. Futures watches are **warn-only** (no futures orders). Alerts skip degraded (`ok=false`) and stale snapshots.

---

## Honesty / known limits

- Scores are **heuristics**, not alpha guarantees.
- MTF is **sampled top-N**, not every symbol on the book.
- Whales = notional size proxy; **not** labeled smart money.
- CI workflow is **`python-lab-only`** (native prefer off). Maturin native CI is not in this pin yet.
- `elapsed_ok` in smoke is **evidence**, not a hard fail gate.
- No 48h soak claim without an on-disk evidence pack (`approx_48h_claim_ok=true`, `hard_fails=0`).
- Opening port `8787` to the internet without reverse-proxy + API key is **operator negligence**.

---

## License & brand

See [`LICENSE`](LICENSE). Brand: **DUP Labs** / **DUP Protocol** companion tooling. Canonical L1 repos remain `Gruver87/dup-protocol` and `Gruver87/dup-protocol-experimental`.

**By using this software you accept [`docs/DISCLAIMER.md`](docs/DISCLAIMER.md).**
