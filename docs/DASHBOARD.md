# Dashboard

Single-page app at `/` (`dashboard/index.html`). Navigation follows the same pattern as the DUP Protocol ops console: **page buttons + hash routes**, not one endless scroll of every panel.

## Pages (hash)

| Hash | Page | Contents |
|------|------|----------|
| `#watchlist` | Watchlist (default) | Sector tabs, trade/auto/Telegram status, watch box, ranked sector table, Buy/Sell |
| `#markets` | Markets ~6m | Ranked **column table** (top-100): rank, symbol, name, price, ~6m change, mcap |
| `#ops` | Ops | World clock (fixed UTC offsets, incl. Minsk UTC+3) + FX (ECB + NBRB for BYN/RUB) |
| `#help` | Help | How to read scores / manual / auto / Telegram |

Sector tabs render **only** on Watchlist. Switching sectors updates the table in place — **no** `scrollIntoView` (avoids jump-to-bottom).

## Markets data

- API: `GET /api/ops/bubbles` (historical path name; payload is coin list + ~6m bps, not a bubble chart).
- UI: plain HTML table sorted by rank. Poll ~30s; Refresh also forces reload.
- Stale / circuit responses surface in the meta line (`STALE`, `err=…`); do not treat as green silently.

## Live updates

- HTTP poll remains the source of truth (snapshot ~60s, markets ~30s, clock ~30s).
- Optional `WS /ws` nudges a snapshot refresh on `snapshot` / `alert` / `trade` (5s debounce). Soft: poll continues if WS fails.

## Language

RU / EN toggle (`localStorage` key `radar_lang`). Sector labels and help copy follow the pack.

## Related

- [`OPS.md`](OPS.md) — how to run the process  
- [`TRADE.md`](TRADE.md) — Buy/Sell and auto gates  
- [`SECURITY.md`](SECURITY.md) — binding / API key  
