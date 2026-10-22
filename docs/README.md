# DUP Market Radar — documentation index

**Product:** DUP Market Radar · **Org:** DUP Labs · **Pin:** `0.2.85`  
**Repo:** https://github.com/Gruver87/dup-market-radar

Read order for operators:

1. [`DISCLAIMER.md`](DISCLAIMER.md) — liability / not financial advice  
2. [`LIVE_MONEY.md`](LIVE_MONEY.md) — before any live capital  
3. [`PROFILES.md`](PROFILES.md) — lab / python-lab / native / live-money  
4. [`OPS.md`](OPS.md) — run / verify / Docker  
5. [`RECOVERY.md`](RECOVERY.md) — self-heal after network / worker death  
6. [`EVIDENCE_MATRIX.md`](EVIDENCE_MATRIX.md) — claim → pack honesty  
7. [`DASHBOARD.md`](DASHBOARD.md) — UI pages  
8. [`TRADE.md`](TRADE.md) — manual / auto / Telegram gates  
9. [`SECURITY.md`](SECURITY.md) — SecretManagerPort, CORS, exposure  
10. [`ARCHITECTURE.md`](ARCHITECTURE.md) — pipeline / native / snapshot  
11. [`EVIDENCE.md`](EVIDENCE.md) — how to produce packs  
12. [`BRAND.md`](BRAND.md) — naming & URLs  

Root:

| File | Role |
|------|------|
| [`../README.md`](../README.md) | Quick start + feature honesty |
| [`../CHANGELOG.md`](../CHANGELOG.md) | Release notes |

## What this is / is not

| Is | Is not |
|----|--------|
| Standalone watchlist + optional KuCoin **spot** trade | DUP Protocol L1 / mainnet node |
| Heuristic rankings + fail-closed auto gates | Guaranteed alpha / broker |
| Lab + operator evidence packs | Automatic 48h soak claim |

Companion L1 repos (separate): [`dup-protocol`](https://github.com/Gruver87/dup-protocol), [`dup-protocol-experimental`](https://github.com/Gruver87/dup-protocol-experimental).

## Current pin snapshot (0.2.85)

| Area | State |
|------|--------|
| Score | `radar_native` prefer + fail-closed in `config/default.yaml`; Docker/CI = python-lab |
| Ingest | Parallel CEX bootstraps; Dex batch + soft-wall; CoinGecko soft-wall; KuCoin symbols deferred |
| MTF | Ensure gate = `ensure_cap`; display patch-first; kline workers 12; budget `max_kline_gets` |
| Evidence | Live smoke packs under `docs/evidence/runs/`; **no 48h soak claim** |
| Trade | Auto default OFF; never futures; fail-closed on `ok=false` / ensure fail |

Pipeline detail: [`ARCHITECTURE.md`](ARCHITECTURE.md). Ops / perf: [`OPS.md`](OPS.md).
