# Agent Skills companion

**Product:** [dup-agent-skills](https://github.com/Gruver87/dup-agent-skills) · **Not** financial advice.

Cursor / compatible agents load markdown skills so they talk about Market Radar **honestly** (ops-ready ≠ soak ≠ live money), refuse **futures auto**, and keep secrets out of chat.

## Quick link into this repo

From `dup-market-radar` (expects sibling `..\dup-agent-skills` on Desktop):

```powershell
.\scripts\link_agent_skills.ps1
```

Copies packs into `.agents/skills/` (gitignored). Re-run after skills upgrades.

Or install user-wide from the skills repo:

```powershell
cd ..\dup-agent-skills
.\scripts\install_user_skills.ps1
.\scripts\install_mcp.ps1
```

## Skills that matter for radar ops

| Skill | Use when |
|-------|----------|
| `radar-ops-status` | Ready / green / soak language |
| `radar-live-money-gate` | Before live spot capital |
| `no-futures-auto` | Any futures auto ask |
| `radar-test-gate` | `test_all` / `go_ready` / CI |
| `radar-pwa-install` | Phone panel (not phone server) |
| `evidence-honesty` | PASS claims need packs |
| `secrets-hygiene` | `.env` / API keys |

## MCP (optional)

Read-only tools against `http://127.0.0.1:8787` — see skills repo [`docs/MCP.md`](https://github.com/Gruver87/dup-agent-skills/blob/main/docs/MCP.md).

## Boundaries

- Skills are **not** a substitute for `go_ready.ps1` / evidence packs.
- Do not mix L1 mesh soak language with radar packs (`l1-boundary` skill).
- Futures auto remains out of scope (`FUTURES_BLOCK`).
