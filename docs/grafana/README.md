# Grafana / Prometheus — DUP Market Radar

**Pin:** import dashboard + scrape `/api/metrics/prometheus`. Not a soak claim.

## 1. Scrape

- Snippet: [`prometheus-scrape.yml`](prometheus-scrape.yml)
- Or compose: repo root `docker-compose.observability.yml` (Prometheus + Grafana)

```powershell
docker compose -f docker-compose.observability.yml up -d
# Prometheus http://127.0.0.1:9090 · Grafana http://127.0.0.1:3000 (admin/admin — change)
```

Radar must be up (`.\scripts\run_radar.ps1`, default `http://127.0.0.1:8787`).  
Green Grafana panels ≠ 48h soak. Non-soak board: `.\scripts\ops_board.ps1`.

## 2. Dashboard

1. Grafana → **Import** → upload [`radar-ops.json`](radar-ops.json)
2. Pick Prometheus datasource (UID/name matching your install)
3. Panels: scan latency, HTTP/circuit, Telegram, auto/trade, recovery

## 3. Honesty

- Metrics are ops gauges — green panels ≠ 48h soak PASS
- Live capital still needs [`../LIVE_MONEY.md`](../LIVE_MONEY.md)
