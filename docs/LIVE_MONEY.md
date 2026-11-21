# Live money checklist (operator)

**Read [`DISCLAIMER.md`](DISCLAIMER.md) first. Author / DUP Labs accept no liability.**

Default config is **lab-safe**: `auto_trade.dry_run: true`, **auto_execute OFF**.  
**Futures are never auto-traded** (`FUTURES_BLOCK` + overlay sectors).

**Auto-execute ≠ watchlist signals.** Dashboard «Автоисполнение: ON» + `armed=true` (keys + `dry_run=false` + kill clear) is what places KuCoin spot market orders without the Preview/Confirm dialog. Manual Buy/Sell always keep confirm. Kill-switch (**СТОП**) latches OFF immediately.

## One command (preflight)

```powershell
.\scripts\go_live_money.ps1
.\scripts\go_live_money.ps1 -Status   # list blockers; exit 0 (not a live PASS)
```

Fail-closed without `.env` KuCoin Spot keys + `RADAR_API_KEY`, `READY_OPS.json` (`ops_ready=true`), and `radar_native`.  
Does **not** turn Auto ON. Does **not** start 48h soak.

## Activate after PASS

```powershell
copy .env.example .env   # then fill keys — never commit .env
# RADAR_API_KEY=<long random> ; RADAR_REQUIRE_API_KEY=1
# KUCOIN_API_KEY / SECRET / PASSPHRASE — Spot Trade + IP whitelist

.\scripts\go_live_money.ps1
$env:RADAR_PROFILE = "live-money"
$env:RADAR_REQUIRE_API_KEY = "1"
.\scripts\run_radar.ps1
```

Dashboard: `localStorage.setItem("radar_api_key", "<RADAR_API_KEY>")`.  
Confirm Watchlist `ok=true` and `mtf_ensure_ok=true`, **then** Автоисполнение ON (spot/memes/listings only). Status must show **ARMED**.

Overlay: `config/live_money.yaml` via `RADAR_PROFILE=live-money`  
(`deployment_mode=prod`, `dry_run=false`, size/position caps, Auto-execute still OFF until you enable).

## Before `dry_run: false` (manual checklist)

0. Host ops-ready: `.\scripts\go_ready.ps1` → `data/evidence/READY_OPS.json` with `ops_ready=true` (see [`OPS_READY.md`](OPS_READY.md))
1. Build native (recommended): `.\scripts\build_native.ps1` (included in `go_ready`)
2. Keys in `.env` (Spot Trade + IP whitelist); never commit `.env`
3. Set `RADAR_API_KEY` and keep `RADAR_REQUIRE_API_KEY=1`
4. Prefer native compose or local prefer+fail_closed — not lab-python Docker for live capital
5. Confirm snapshot `ok=true` and `mtf_ensure_ok=true` on dashboard **Watchlist** (`#watchlist`)
6. Prefer `.\scripts\verify.ps1 -Profile PreSoak` (or at least `--live --evidence`) and keep the pack
7. Use `RADAR_PROFILE=live-money` (or set `auto_trade.dry_run: false` in an overlay — do not weaken lab default)
8. Enable auto-execute only after a dry_run cycle with expected actions in the log (lab) then live profile
9. Keep position size + daily caps low (`quote_usdt_micros_per_buy`, `max_daily_buys`, `max_daily_notional_usdt_micros`, `signal_cooldown_sec`)
10. Know the kill-switch: dashboard **СТОП** or `POST /api/trade/auto/kill`

## Memes auto

- **CEX** keyword memes (KuCoin-first) are eligible when `memes` is in `allowed_sectors` and `include_cex_spot: true`
- **DEX** DexScreener rows remain watchlist-only (`_collect_buys` skips dexscreener)

## Rollback

Hit kill-switch / Автоисполнение OFF immediately, or unset `RADAR_PROFILE` (back to lab `dry_run: true`). Stops still run on degrade via mark when bags are open **only while auto_execute remains enabled**; kill-switch stops the cycle entirely.

## Soak evidence

48h soak is **separate** and only after operator approval. Short soak before larger size:

```powershell
python scripts/radar_soak_lab.py --cycles 5 --interval-sec 60
```

Require `hard_fails=0` in `data/evidence/LATEST_SOAK.json`.
