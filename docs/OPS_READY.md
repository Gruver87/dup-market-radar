# Ops-ready definition — DUP Market Radar

**Pin:** `0.2.89` · see root `pyproject.toml` / `src/radar/__init__.py`  
**Not** a 48h soak claim. **Not** financial advice — [`DISCLAIMER.md`](DISCLAIMER.md).

## What “fully ready to work” means here

| Ready | Meaning |
|-------|---------|
| **Ops-ready** | `.\scripts\go_ready.ps1` → exit 0; `data/evidence/READY_OPS.json` has `ops_ready=true` |
| **Watchlist / manual / auto dry_run** | Server runs; dashboard; auto default OFF; `dry_run: true` |
| **Live capital** | Extra operator steps in [`LIVE_MONEY.md`](LIVE_MONEY.md) — keys, native, `dry_run: false` |
| **48h soak claim** | Separate: `approx_48h_claim_ok=true` **and** `hard_fails=0` only |

## One command

```powershell
cd dup-market-radar
.\scripts\go_ready.ps1
# or faster if native already built:
.\scripts\go_ready.ps1 -SkipBuild
```

Then:

```powershell
.\scripts\run_radar.ps1
```

Open http://127.0.0.1:8787/

## Already enforced in code (do not regress)

- Auto refuses buys on `ok=false` / `mtf_ensure_ok=false` / native-prefer + python backend
- Open bags on degrade → **stop-only** via KuCoin mark
- Telegram alerts skip `ok=false`; `/buy` `/sell` in `/help`
- Dashboard shows MTF ensure / circuit tags
- CI: `ci.yml` (python-lab) + `ci-native.yml` (maturin parity)
- Integer money; CORS deny `*`; trade API key gate

## 48h soak honesty (this tree)

A wall-clock ~48h lab run may exist under `data/evidence/LATEST_SOAK.json` with `hard_fails≥1` (e.g. DNS/`getaddrinfo` outage). That pack stays **`approx_48h_claim_ok=false`**. Ops-ready does **not** require a green 48h claim.

Optional later:

```powershell
.\scripts\verify.ps1 -Profile Soak48h
```

## Related

- [`LIVE_MONEY.md`](LIVE_MONEY.md) — before `dry_run: false` / `.\scripts\go_live_money.ps1`
- [`PWA.md`](PWA.md) — Android installable dashboard (remote panel; host stays the server)
- [`grafana/README.md`](grafana/README.md) — Prometheus scrape + dashboard import
- [`OPS.md`](OPS.md) — profiles / Docker
- [`EVIDENCE_MATRIX.md`](EVIDENCE_MATRIX.md) — claim → pack
- [`TRADE.md`](TRADE.md) — auto / Telegram
