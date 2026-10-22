# Operations

## Local process

```powershell
.\scripts\build_native.ps1   # optional but recommended (run_radar does NOT rebuild native)
.\scripts\run_radar.ps1              # stable: no uvicorn --reload
.\scripts\run_radar.ps1 -Reload      # dev only: watches src/config/dashboard (.venv excluded)
```

Default UI: `http://127.0.0.1:8787/` — pages `#watchlist` / `#markets` / `#ops` / `#help` (see [`DASHBOARD.md`](DASHBOARD.md)).

**Reload / `~adar_native`:** bare `--reload` watches the whole tree including `.venv`. A locked `radar_native` `.pyd` + pip rename creates `~adar_native` and can cascade reloads. Default `run_radar.ps1` is reload-off; `-Reload` scopes watch dirs and excludes `.venv`.

## Profiles

| Profile | How | Native | Use |
|---------|-----|--------|-----|
| **default.yaml** | Local with wheel built | prefer + fail_closed | Closest to “honest native” |
| **python-lab** | Docker compose + `ci.yml` | prefer=0 fail_closed=0 | Labs / full unit suite, no maturin in image |
| **ci-native** | `.github/workflows/ci-native.yml` | prefer+fail_closed | Maturin build + score parity only |
| **live smoke** | `radar_smoke_lab.py --live` | often soft | One-shot evidence |

GitHub Actions (both on PR + `main`):

| Workflow | Job | What it proves |
|----------|-----|----------------|
| `ci.yml` | `test (python-lab-only)` | Full pytest + offline smoke, native prefer **off** |
| `ci-native.yml` | `maturin + score parity` | Wheel builds; `test_score_parity` / non-crypto fut with prefer **on** |

Do **not** claim “CI = full native prod” from ci-native alone (parity subset, not full suite under native).

## Health

- `/api/live` — process up  
- `/api/ready` — warm + persist OK  
- `/api/health` — scan / native / circuit honesty  
- `/api/metrics` — JSON; Prometheus text at `/api/metrics/prometheus`

Dashboard Watchlist status line surfaces `СБОЙ ДАННЫХ`, `mtf=OK|FAIL`, and ensure fill ratios when present.

## Data dir

`data/` holds positions, auto state, watches, oid ledger, trade events (gitignored JSON/JSONL). Back up before wiping. Corrupt `auto_positions.json` **blocks enabling** auto-trade.

## Smoke

```powershell
$env:PYTHONPATH="src"
python scripts/radar_smoke_lab.py          # offline
python scripts/radar_smoke_lab.py --live   # network
python scripts/radar_warm_smoke_lab.py     # warm stale-OK + MTF cache
```

Operator full local check (**no soak**):

```powershell
.\scripts\test_all.ps1
# or: .\scripts\verify.ps1 -Profile Full
.\scripts\verify.ps1 -Profile List
```

`elapsed_ok` vs ~25s is **evidence**, not a hard CI fail.

## Known soft fails (not Full-FAIL)

| Symptom | Meaning | Operator action |
|---------|---------|-----------------|
| `~adar_native` under `.venv\Lib\site-packages` | `.pyd` locked (often by `run_radar` + broad `--reload` / pip) | stop server → `build_native.ps1` or `run_radar.ps1` startup cleanup; use reload-off or `-Reload` (no `.venv` watch) |
| CoinGecko `429` / `stables: 0` | free-tier rate limit | accepted soft; stables poll 180s + stale-OK; optional API key later |
| Binance kline `400` (AKE/ILY/…) | delisted / invalid symbol | **done** — process denylist (`_kline_skip_persist`); one `info` on first refuse, then silent skip + `kline_skip_hits`; waterfall covers |
| Starlette `httpx` TestClient deprecation | pytest warning | migrate to `httpx2` later — not a gate |

---

## Docker

```powershell
docker compose up --build
```

- Publishes `127.0.0.1:8787` by default.  
- Secrets via env / `.env` — never baked into the image.  
- Lab-python native overrides — see compose header comments.

## Verify (unified entry)

```powershell
.\scripts\verify.ps1 -Profile List
.\scripts\verify.ps1 -Profile Quick      # pytest + smoke offline
.\scripts\verify.ps1 -Profile Lab        # + live evidence (default)
.\scripts\verify.ps1 -Profile PreSoak    # + native + short soak
.\scripts\verify.ps1 -Profile Soak48h    # PreSoak then 48h
```

Wrappers (same gates): `pre_soak_gate.ps1`, `radar_soak_48h.ps1`.

| Profile | Starts 48h? | Claim soak? |
|---------|-------------|-------------|
| Quick / Lab / PreSoak | No | No |
| Soak48h | Yes (after PreSoak) | Only if pack `approx_48h_claim_ok=true` and `hard_fails=0` |

## No soak claim

Do not report “48h soak passed” unless you ran it and stored an evidence pack on disk with `approx_48h_claim_ok=true`.

## Native Docker

```powershell
docker compose -f docker-compose.native.yml up --build
```

Uses `Dockerfile.native` (maturin wheel, `RADAR_NATIVE_PREFER=1`). Lab compose remains python-only.

## Ops HTTP helpers

| Path | Notes |
|------|-------|
| `GET /api/ops/worldtime` | Fixed offsets; Minsk = UTC+3 |
| `GET /api/ops/fx` | ECB (Frankfurter) + NBRB for BYN/RUB |
| `GET /api/ops/bubbles` | Top-100 ~6m payload for Markets table |
| `GET /api/ops/recovery` | Self-recovery supervisor (watchdog + scan) |

Self-heal details: [`RECOVERY.md`](RECOVERY.md).

## Evidence

```powershell
python scripts/radar_smoke_lab.py --live --evidence
```

See [`EVIDENCE.md`](EVIDENCE.md) and [`EVIDENCE_MATRIX.md`](EVIDENCE_MATRIX.md).

## Performance notes (pin 0.2.87)

Honesty only — **not** a soak gate. Live RTT dominates.

| Phase | What to read | Typical levers |
|-------|----------------|----------------|
| Ingest | `ingest_ms`, `venue_ms.*` | Parallel venue bootstraps; Dex/CG walls; KuCoin stale-OK; optional `RADAR_PROFILE=light` |
| Ensure | `ensure_ms`, `short_window_ensure.stage_ms`, `display_ensure_ms` | MTF process cache (0.2.87); skip ≥0.85; low-board / base display walls |
| Score | `score_ms` | Native prefer; display time must **not** inflate `score_ms` |

Best recent live smoke in this series: packaged `smoke-0.2.79` (~6.5s elapsed). Later pins trade coverage / honesty vs wall-clock; KuCoin `allTickers` remains the usual ingest ceiling. **48h soak not claimed.**
