# Agent Skills companion

**Product:** [dup-agent-skills](https://github.com/Gruver87/dup-agent-skills) (≥ **0.1.6**) · **Not** financial advice.

Cursor / compatible agents load markdown skills so they talk about Market Radar **honestly** (ops-ready ≠ soak ≠ live money), refuse **futures auto**, and keep secrets out of chat.

## Quick link into this repo

From `dup-market-radar` (expects sibling `..\dup-agent-skills` on Desktop):

```powershell
.\scripts\link_agent_skills.ps1
```

Or from the skills repo (radar + trading-bot):

```powershell
cd ..\dup-agent-skills
.\scripts\link_companions.ps1
.\scripts\install_mcp.ps1
```

Copies packs into `.agents/skills/` (gitignored). Re-run after skills upgrades.

## Skills that matter for radar ops

| Skill | Use when |
|-------|----------|
| `radar-ops-status` | Ready / green / soak language |
| `radar-live-money-gate` | Before live spot capital |
| `no-futures-auto` | Any futures auto ask |
| `radar-test-gate` | `test_all` / `go_ready` / `verify` / CI |
| `radar-pwa-install` | Phone panel (not phone server) |
| `evidence-honesty` | PASS claims need packs |
| `secrets-hygiene` | `.env` / API keys |

## MCP (optional, read-only)

Against `http://127.0.0.1:8787` — detail [`docs/MCP.md`](https://github.com/Gruver87/dup-agent-skills/blob/main/docs/MCP.md):

| Tool | Endpoint |
|------|----------|
| `radar_health` | GET `/api/health` |
| `radar_ready` | GET `/api/ready` |
| `radar_snapshot_summary` | GET `/api/snapshot` |
| `radar_metrics_up` | GET `/api/metrics` |
| `radar_trade_paper_status` | GET `/api/trade/paper` |
| `radar_trade_status` | GET `/api/trade/status` |
| `radar_telegram_status` | GET `/api/telegram/status` |
| `radar_recovery_status` | GET `/api/ops/recovery` |

Never place-order / enable auto / futures. Lab: `python scripts/radar_mcp_lab.py` in skills repo (SKIP if radar down).

## Operator gates (this repo)

```powershell
.\scripts\verify.ps1 -Profile Quick   # gate + pytest + offline smoke — NO soak
.\scripts\verify.ps1 -Profile Full    # + warm/live — still NO soak
.\scripts\go_ready.ps1                # ops-ready ≠ 48h
```

## Boundaries

- Skills are **not** a substitute for `go_ready.ps1` / evidence packs.
- Do not mix L1 mesh soak language with radar packs (`l1-boundary` skill).
- Futures auto remains out of scope (`FUTURES_BLOCK`).
