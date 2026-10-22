# Named profiles — DUP Market Radar

ADR 0016 spirit for a **companion** product (not L1 FEATURE sprouts).

| Profile | How | Native | Secrets | Use |
|---------|-----|--------|---------|-----|
| **lab** | `deployment_mode: lab` (default) | soft OK | `secrets.backend=env` or file | Local dig, pytest |
| **python-lab** | Docker `docker-compose.yml` / CI | `RADAR_NATIVE_*=0` | env | CI, no maturin wheel |
| **native** | `build_native.ps1` or `docker-compose.native.yml` | prefer+fail_closed | env | Honest scoring |
| **live-money** | Operator overlay + keys | prefer+fail_closed | **env only** (`deployment_mode=prod` refuses file) | Real capital — see [`LIVE_MONEY.md`](LIVE_MONEY.md) |

## Rules

1. Do **not** mix profiles in one claim (e.g. python-lab smoke ≠ native-prod readiness).
2. `deployment_mode=prod` + `secrets.backend=file` → config validation **refuses**.
3. Auto `dry_run: true` remains default in `config/default.yaml`.
4. Gate: `python scripts/radar_gate.py`.

Env overlays:

- `RADAR_DEPLOYMENT_MODE=prod|lab`
- `RADAR_SECRET_BACKEND=env|file`
- `RADAR_NATIVE_PREFER` / `RADAR_NATIVE_FAIL_CLOSED`
- `RADAR_LOG_JSON=1` — structured JSON logs

## Related

[`OPS.md`](OPS.md) · [`SECURITY.md`](SECURITY.md) · [`EVIDENCE_MATRIX.md`](EVIDENCE_MATRIX.md)
