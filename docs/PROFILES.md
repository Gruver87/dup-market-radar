# Named profiles — DUP Market Radar

ADR 0016 spirit for a **companion** product (not L1 FEATURE sprouts).

| Profile | How | Native | Secrets | Use |
|---------|-----|--------|---------|-----|
| **lab** | `deployment_mode: lab` (default) | soft OK | `secrets.backend=env` or file | Local dig, pytest |
| **python-lab** | Docker `docker-compose.yml` / `ci.yml` | `RADAR_NATIVE_*=0` | env | Full unit suite, no maturin wheel |
| **ci-native** | `.github/workflows/ci-native.yml` | prefer+fail_closed | env | Maturin + score parity (not full suite) |
| **native** | `build_native.ps1` or `docker-compose.native.yml` | prefer+fail_closed | env | Local / compose honest scoring |
| **light** | `RADAR_PROFILE=light` → overlay `config/light.yaml` | inherits default | env | KuCoin-primary fast refresh (0.2.87) |
| **live-money** | Operator overlay + keys | prefer+fail_closed | **env only** (`deployment_mode=prod` refuses file) | Real capital — see [`LIVE_MONEY.md`](LIVE_MONEY.md) |

## Rules

1. Do **not** mix profiles in one claim (e.g. python-lab smoke ≠ native-prod readiness; ci-native parity ≠ full native CI suite).
2. `deployment_mode=prod` + `secrets.backend=file` → config validation **refuses**.
3. Auto `dry_run: true` remains default in `config/default.yaml`.
4. Gate: `python scripts/radar_gate.py`.
5. **light** is an ingest overlay only — not a money/readiness claim; full multi-venue remains `default.yaml`.

Env overlays:

- `RADAR_DEPLOYMENT_MODE=prod|lab`
- `RADAR_SECRET_BACKEND=env|file`
- `RADAR_NATIVE_PREFER` / `RADAR_NATIVE_FAIL_CLOSED`
- `RADAR_PROFILE=light` — merge `config/light.yaml` onto default
- `RADAR_CONFIG` / `RADAR_CONFIG_PATH` — full alternate YAML (skips light overlay)
- `RADAR_LOG_JSON=1` — structured JSON logs

## Related

[`OPS.md`](OPS.md) · [`SECURITY.md`](SECURITY.md) · [`EVIDENCE_MATRIX.md`](EVIDENCE_MATRIX.md)
