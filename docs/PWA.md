# PWA — phone panel for DUP Market Radar

**Pin:** same as app · **Not** a server inside Android. **Not** financial advice.

The dashboard can be **installed** on Android (Chrome) as a home-screen app.  
It is a **remote panel** to your host radar (PC/VPS). Auto-trade and keys stay on the host. Futures auto stays OFF.

## Install (Android)

1. Run radar on the host: `.\scripts\run_radar.ps1`
2. Open the dashboard URL in **Chrome** on the phone  
   - Same LAN: `http://<pc-lan-ip>:8787/`  
   - From internet: put **HTTPS** in front (Cloudflare Tunnel / Caddy / nginx) — browsers prefer HTTPS for install
3. Chrome menu → **Add to Home screen** / **Install app**  
   - Or tap **Установить** if the in-page banner appears
4. Optional: `localStorage.setItem("radar_api_key", "<RADAR_API_KEY>")` in desktop DevTools once, or set the key on the phone browser before install

## What is cached

Service worker `sw.js` caches the **shell** (HTML/icons) only.  
`/api/*` and `/ws` are **never** cached — no offline trading.

## Honest limits

| OK | Not this PWA |
|----|----------------|
| Watchlist / Ops / Markets UI | Radar process inside the phone |
| Telegram remains a separate remote | Moving KuCoin keys onto the device as “the server” |
| HTTPS reverse-proxy to your host | Claiming 48h soak from the phone |

## Related

[`OPS.md`](OPS.md) · [`LIVE_MONEY.md`](LIVE_MONEY.md) · [`TRADE.md`](TRADE.md)
