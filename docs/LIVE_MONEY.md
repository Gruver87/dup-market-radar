# Live money checklist (operator)

**Read [`DISCLAIMER.md`](DISCLAIMER.md) first. Author / DUP Labs accept no liability.**

Default config is **lab-safe**: `auto_trade.dry_run: true`, auto **OFF**.

## Before `dry_run: false`

1. Build native (recommended): `.\scripts\build_native.ps1`
2. Keys in `.env` (Spot Trade + IP whitelist); never commit `.env`
3. Set `RADAR_API_KEY` and keep `RADAR_REQUIRE_API_KEY=1`
4. Prefer native compose or local prefer+fail_closed — not lab-python Docker for live capital
5. Confirm snapshot `ok=true` and `mtf_ensure_ok=true` on dashboard
6. Run `python scripts/radar_smoke_lab.py --live --evidence` and keep the pack
7. In `config/default.yaml` (or overlay): set `auto_trade.dry_run: false`
8. Enable auto only after a dry_run cycle with expected actions in the log
9. Keep position size caps low (`quote_usdt_micros_per_buy`)

## Memes auto

- **CEX** keyword memes (KuCoin-first) are eligible when `memes` is in `allowed_sectors` and `include_cex_spot: true`
- **DEX** DexScreener rows remain watchlist-only (`_collect_buys` skips dexscreener)

## Rollback

Set `dry_run: true` or Auto OFF immediately. Stops still run on degrade via mark when bags are open.
