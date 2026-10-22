# Security

## Secrets

- Copy `.env.example` → `.env`. **Never commit `.env`.**
- **SecretManagerPort** (`radar.secret_mgmt`): logical ids → env (default) or file (lab only).
- `deployment_mode=prod` (or `RADAR_DEPLOYMENT_MODE=prod`) **refuses** `secrets.backend=file`.
- KuCoin: Spot Trade permission; prefer IP whitelist; passphrase required.
- Optional `RADAR_API_KEY` — required for trade mutations when `RADAR_REQUIRE_API_KEY=1` (compose default).
- Logs and audit JSONL pass through `radar.secrets_redact`. `RADAR_LOG_JSON=1` for structured JSON logs.

## Network

- Outbound HTTP: **HTTPS allowlist** only; `verify=True`; no redirects on signed calls.
- Response body size caps (ingest / signed / meta).
- CORS: explicit origins only — **never** `*`.
- TrustedHost middleware; OpenAPI off unless `RADAR_ENABLE_OPENAPI=1`.

## Rate limits

Trade, force-scan, snapshot, ops, metrics, probe, and auth-fail paths are rate-limited. Soft-refuse (no hard IP ban by default).

## Exposure

| Bad | Better |
|-----|--------|
| Bind `0.0.0.0:8787` on a VPS without proxy | Loopback + reverse proxy + TLS + API key |
| Commit keys / paste tokens in issues | Rotate immediately; use `.env` only |
| Disable TLS verify | Never |

`/api/snapshot` is readable without API key by design (local dashboard). Treat it as **sensitive ops data** if the port is reachable off-machine.

## Disclaimer

Security hardening does **not** transfer liability. See [`DISCLAIMER.md`](DISCLAIMER.md).

## Snapshot auth

- `api.snapshot_require_api_key` — always require key for `/api/snapshot` and `/api/sector`.
- `api.snapshot_require_api_key_non_loopback` (default true) — when `RADAR_API_KEY` is set, remote clients must send `X-Radar-Api-Key`; loopback dashboard stays usable without the header.
- Native compose sets `RADAR_SNAPSHOT_REQUIRE_API_KEY=1` by default (env overlay).
- WebSocket `/ws` uses the same key gate when snapshot/trade key is required (`?api_key=` or `X-Radar-Api-Key`).
- Webhook URL only from env (`RADAR_WEBHOOK_URL` / `DISCORD_WEBHOOK_URL`); HTTPS host allowlist (Discord/Slack).
