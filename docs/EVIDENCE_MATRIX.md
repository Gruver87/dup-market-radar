# Evidence matrix — DUP Market Radar

**Pin:** 0.2.76 · Companion product — **not** L1 mesh evidence.

Claims require on-disk packs. Missing pack ⇒ **⏳** / do not invent PASS.

| Claim | Gate / artifact | Status rule |
|-------|-----------------|-------------|
| Unit suite green | `pytest` | CI / local; not soak |
| Live smoke | `radar_smoke_lab.py --live --evidence` → `data/evidence/smoke_live_*.json` | `ok=true` in pack |
| Pre-soak | `verify.ps1 -Profile PreSoak` | exit 0; short soak `hard_fails=0` |
| Short soak | `radar_soak_lab.py --cycles N` → `LATEST_SOAK.json` | `hard_fails=0`; **not** 48h |
| **48h soak** | `radar_soak_48h.ps1` + pack | `approx_48h_claim_ok=true` **and** `hard_fails=0` only |
| Native prefer | `score_backend=radar_native` in smoke/soak | Lab-python compose does **not** count |
| Hashed run package | `scripts/package_radar_evidence.py` → `docs/evidence/runs/<id>/` | `manifest.status`; missing inputs ⇒ `incomplete` |
| Static posture | `python scripts/radar_gate.py` | PASS needles (secrets/recovery/profiles) |
| Phase timers (0.2.69+) | snapshot `ingest_ms`/`ensure_ms`/`score_ms` + Prom gauges | honesty only — not a soak gate |
| Venue / ensure stage ms (0.2.71+) | `venue_ms`, `short_window_ensure.stage_ms` + Prom | honesty — not soak |
| Smoke phase export (0.2.72) | `radar_smoke_lab` summary: `ingest_ms`/`ensure_ms`/`venue_ms`/`kline_gets` | pack must include fields |
| Oid write-behind (0.2.72) | `claim` sync; `mark_accepted`/`release` debounce + `close()` flush | unit tests; not soak |
| Ensure gate freeze (0.2.72) | fill ratios vs frozen top-N (no mid-ensure re-rank) | live smoke `mtf_ensure_ok` |
| Venue bootstrap parallel (0.2.73) | Binance 4-way + KuCoin 3-way bootstrap; `venue_ms` drop | live smoke vs prior pack |
| Whales aggregate-first (0.2.74) | bucket sum ≥ `min_notional`; KuCoin `symbols_watch` | live smoke `counts.whales` > 0 |
| Dex batch token expand (0.2.75) | `/tokens/{a,b,...}` one GET for boosted cap | live `venue_ms.dexscreener` drop |
| Display MTF patch (0.2.76) | post-score ensure + patch metrics; kline 400 denylist | live spot_mtf coverage |
| Ensure GET budget | `ingest.max_kline_gets` + `http_stats.kline_gets` | incomplete fill still fail-closed |

## Packaging

```powershell
python scripts/package_radar_evidence.py --from-latest --label smoke
```

Creates `docs/evidence/runs/<utc>-<label>/` with `manifest.json` + sha256.  
**Never** claim 48h unless `manifest.status=pass_48h`.

## Related

- [`EVIDENCE.md`](EVIDENCE.md) — how to produce packs  
- [`PROFILES.md`](PROFILES.md) — lab vs native vs live-money  
- [`RECOVERY.md`](RECOVERY.md) — self-heal (not soak evidence)  
- [`DISCLAIMER.md`](DISCLAIMER.md)  
