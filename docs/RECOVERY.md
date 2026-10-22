# Self-recovery

**Pin:** 0.2.80 · Index: [`README.md`](README.md)

When the network drops, a venue flaps, or a worker thread dies, Radar must **come back by itself** without silent green or auto-enabling trade.

## What already existed (partial)

| Mechanism | Behavior |
|-----------|----------|
| HTTP retries + backoff | Per-request soft retry (`ingest.http_retries`) |
| Venue circuit breaker | Soft-open after N fails; half-open after `circuit_open_ms` |
| Stale FX / Markets cache | Soft-serve last good book with `stale=true` |
| Auto fail-closed | No buys on `ok=false` / ensure fail; stop-only if bags open |
| Telegram poll | Continues after `getUpdates` error (3s wait) |
| Docker `restart: unless-stopped` | Container process restart only |

## Supervisor behavior

`radar.recovery.RecoverySupervisor` (daemon `radar-recovery`):

1. **Background scan** — refreshes snapshot on `snapshot_refresh_ms` cadence even with zero HTTP traffic; after consecutive fails, probes with exponential gap until OK.
2. **Network recovered** — after ≥2 consecutive scan fails, first `ok=true` scan emits event + optional Telegram broadcast + WS `type=recovery`.
3. **Auto thread watchdog** — if `radar-auto-trade` dies → `AutoTradeEngine.start()` again (budget-capped).
4. **Telegram watchdog** — if poll/alert workers die → `TelegramBot.ensure_running()` (stop→start).
5. **Dashboard WS** — exponential reconnect on `onclose` (HTTP poll remains source of truth).

## Invariants (fail-closed)

- Recovery **never** sets `auto_trade.enabled=true`.
- Recovery **never** clears venue circuits or paints `ok=true` on a bad scan.
- Restart thrash soft-refused via `max_restarts_per_hour`.

## Config (`config/default.yaml`)

```yaml
recovery:
  enabled: true
  tick_sec: 15
  background_scan: true
  restart_auto_thread: true
  restart_telegram: true
  max_restarts_per_hour: 20
  restart_backoff_ms: 2000
  notify_on_network_recovered: true
```

## Ops surfaces

| Path | Role |
|------|------|
| `GET /api/ops/recovery` | Supervisor status + recent events |
| `GET /api/health` → `recovery` | Embedded honesty block |
| Health problems | `auto_loop_dead`, `telegram_threads_dead`, `recovery_supervisor_dead`, `recovery_scan_failing` |

## Related

- [`OPS.md`](OPS.md) — run / verify  
- [`TRADE.md`](TRADE.md) — fail-closed auto gates  
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — layers  
