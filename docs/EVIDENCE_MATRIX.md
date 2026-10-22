# Evidence matrix — DUP Market Radar

**Pin:** 0.2.86 · Companion product — **not** L1 mesh evidence.

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
| Venue bootstrap parallel (0.2.73) | Binance 4-way + KuCoin tickers∥contracts (+ symbols); `venue_ms` | live smoke vs prior pack |
| Whales aggregate-first (0.2.74) | bucket sum ≥ `min_notional`; KuCoin `symbols_watch` | live smoke `counts.whales` > 0 |
| Dex batch token expand (0.2.75) | `/tokens/{a,b,...}` one GET for boosted cap | live `venue_ms.dexscreener` |
| Display MTF patch (0.2.76) | post-score ensure + patch metrics; kline 400 denylist | live spot_mtf coverage |
| MEXC/Bybit bootstrap parallel (0.2.77) | 3-way venue GETs; display time in `ensure_ms` | live `venue_ms` |
| Ensure gate widen + display acc preserve + Dex skip (0.2.78) | `ensure_rank_n`; no clobber; Dex skip @≥12 pairs | live `ensure_ms` / `short_window_ensure` |
| Ensure rank = ensure_cap (0.2.79) | `ensure_rank_n` fills cap; fewer display misses | live `display_ensure_ms` |
| Display patch-first + kline×12 (0.2.80) | pre-patch before miss; workers 12; cap stays 8 | live `display_ensure_ms` / `ensure_ms` |
| Secondary HTTP soft wall (0.2.81) | Dex/CG timeout 2500ms one-shot | live `venue_ms.dexscreener` / `coingecko` |
| KuCoin bootstrap + httpx pool (0.2.82) | symbols after tickers∥contracts; Limits 32/16 | live `venue_ms.kucoin` |
| Display skip on board coverage (0.2.83) | skip display GETs when board fill >=0.85 post-patch | live display_skipped_coverage / display_ensure_ms |
| Whales after KuCoin tickers (0.2.84) | phase2 symbols||whales after allTickers||contracts | live venue_ms.kucoin |
| Display ensure soft wall (0.2.85) | display_ensure_wall_ms=1500 timeout | live display_ensure_timeout_ms |
| KuCoin stale-OK + display low-wall + secondary venue wall (0.2.86) | stale_ok_hits; display_ensure_wall_reason; secondary_venue_wall | live warm smoke + http_stats |
| Ensure GET budget | `ingest.max_kline_gets` + `http_stats.kline_gets` | incomplete fill still fail-closed |

## Latest packaged smokes (operator)

| Label | Pack path | Notes |
|-------|-----------|-------|
| smoke-0.2.86 | [`evidence/runs/20261022T191118Z-smoke-0.2.86`](evidence/runs/20261022T191118Z-smoke-0.2.86/) | warm stale_ok=2; KuCoin ~2.2s; display low/base wall |
| smoke-0.2.85 | [`evidence/runs/20261022T184701Z-smoke-0.2.85`](evidence/runs/20261022T184701Z-smoke-0.2.85/) | display soft wall |
| smoke-0.2.81 | [`evidence/runs/20261022T182004Z-smoke-0.2.81`](evidence/runs/20261022T182004Z-smoke-0.2.81/) | secondary soft wall |
| smoke-0.2.80 | [`evidence/runs/20261022T181428Z-smoke-0.2.80`](evidence/runs/20261022T181428Z-smoke-0.2.80/) | display patch-first |
| smoke-0.2.79 | [`evidence/runs/20261022T180604Z-smoke-0.2.79`](evidence/runs/20261022T180604Z-smoke-0.2.79/) | ensure_rank = cap (~6.5s best) |

**48h soak:** not claimed on this pin series.

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
