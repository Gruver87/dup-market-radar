# Evidence matrix — DUP Market Radar

**Pin:** 0.2.68 · Companion product — **not** L1 mesh evidence.

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
