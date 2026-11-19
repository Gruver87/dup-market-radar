//! Binance spot combined trade stream → Tick.

use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use futures_util::{SinkExt, StreamExt};
use radar_core::{price_i_from_str, qty_i_from_str, Side, Tick, Venue};
use serde_json::Value;
use tokio::sync::mpsc;
use tokio::time::{sleep, timeout};
use tokio_tungstenite::{connect_async, tungstenite::Message};
use url::Url;

use crate::ws_time::{connect_budget, deadline_elapsed, session_ending, READ_IDLE};

const DEFAULT_WS: &str = "wss://stream.binance.com:9443";

#[derive(Debug, Clone)]
pub struct BinanceIngestConfig {
    pub symbols: Vec<String>,
    pub ws_base: String,
    /// Max reconnect attempts (0 = unlimited until deadline).
    pub max_reconnects: u32,
}

impl Default for BinanceIngestConfig {
    fn default() -> Self {
        Self {
            symbols: vec![
                "BTCUSDT".into(),
                "ETHUSDT".into(),
                "SOLUSDT".into(),
            ],
            ws_base: DEFAULT_WS.into(),
            max_reconnects: 8,
        }
    }
}

fn stream_path(symbols: &[String]) -> String {
    let parts: Vec<String> = symbols
        .iter()
        .map(|s| format!("{}@trade", s.trim().to_ascii_lowercase()))
        .collect();
    format!("/stream?streams={}", parts.join("/"))
}

/// Parse combined-stream or raw trade payload into a Tick.
pub fn parse_binance_trade_msg(text: &str) -> Option<Tick> {
    let v: Value = serde_json::from_str(text).ok()?;
    let data = if v.get("stream").is_some() {
        v.get("data")?
    } else {
        &v
    };
    if data.get("e").and_then(|e| e.as_str()) != Some("trade") {
        return None;
    }
    let symbol = data.get("s")?.as_str()?.to_ascii_uppercase();
    let price = price_i_from_str(data.get("p")?.as_str()?)?;
    let qty = qty_i_from_str(data.get("q")?.as_str()?)?;
    let ts_ms = data
        .get("T")
        .and_then(|t| t.as_i64())
        .or_else(|| data.get("E").and_then(|t| t.as_i64()))?;
    let side = match data.get("m").and_then(|m| m.as_bool()) {
        // m=true → buyer is maker → aggressor sell
        Some(true) => Side::Sell,
        Some(false) => Side::Buy,
        None => Side::Unknown,
    };
    let trade_id = data
        .get("t")
        .map(|t| t.to_string().trim_matches('"').to_string());
    Some(Tick {
        venue: Venue::Binance,
        symbol,
        price_i: price,
        qty_i: qty,
        ts_ms,
        side,
        trade_id,
    })
}

/// Connect and forward ticks until `duration` elapses (or forever if zero) / channel closes.
pub async fn run_binance_trades(
    cfg: BinanceIngestConfig,
    duration: Duration,
    tx: mpsc::Sender<Tick>,
) -> Result<u64> {
    if cfg.symbols.is_empty() {
        return Err(anyhow!("no symbols"));
    }
    let forever = duration.is_zero();
    let deadline = if forever {
        None
    } else {
        Some(tokio::time::Instant::now() + duration)
    };
    let mut reconnects = 0u32;
    let mut total = 0u64;

    let mut attempts = 0u32;
    loop {
        if deadline_elapsed(deadline) {
            break;
        }
        // First attempt always; later only if enough time for a real reconnect.
        if attempts > 0 && session_ending(deadline) {
            break;
        }
        attempts += 1;
        let path = stream_path(&cfg.symbols);
        let url = format!("{}{}", cfg.ws_base.trim_end_matches('/'), path);
        let url = Url::parse(&url).context("ws url")?;

        match connect_and_pump(url, deadline, &tx).await {
            Ok(n) => {
                total += n;
                if n > 0 {
                    reconnects = 0;
                }
                if deadline_elapsed(deadline) {
                    break;
                }
                // Clean close with ticks still in window — soft pause then resume
                if n == 0 && !forever {
                    break;
                }
            }
            Err(e) => {
                if deadline_elapsed(deadline) || session_ending(deadline) {
                    break;
                }
                reconnects += 1;
                if cfg.max_reconnects > 0 && reconnects > cfg.max_reconnects {
                    return Err(e).context("binance ws reconnects exhausted");
                }
                eprintln!("radar_ingest binance reconnect: {e:#}");
                sleep(Duration::from_millis(500 * u64::from(reconnects.min(10)))).await;
            }
        }
    }
    Ok(total)
}

async fn connect_and_pump(
    url: Url,
    deadline: Option<tokio::time::Instant>,
    tx: &mpsc::Sender<Tick>,
) -> Result<u64> {
    let Some(budget) = connect_budget(deadline) else {
        return Ok(0);
    };
    let (ws, _) = timeout(budget, connect_async(url.as_str()))
        .await
        .context("connect timeout")?
        .context("connect_async")?;
    let (mut write, mut read) = ws.split();
    let mut n = 0u64;

    loop {
        let left = match deadline {
            Some(d) => {
                let rem = d.saturating_duration_since(tokio::time::Instant::now());
                if rem.is_zero() {
                    let _ = write.close().await;
                    break;
                }
                rem.min(READ_IDLE)
            }
            None => READ_IDLE,
        };
        let msg = match timeout(left, read.next()).await {
            Ok(Some(Ok(m))) => m,
            Ok(Some(Err(e))) => return Err(e.into()),
            Ok(None) => break,
            Err(_) => {
                if deadline.is_some() {
                    break; // session window elapsed while idle
                }
                continue; // forever: keep waiting (server ping handled below)
            }
        };
        match msg {
            Message::Text(text) => {
                if let Some(tick) = parse_binance_trade_msg(&text) {
                    if tx.send(tick).await.is_err() {
                        break;
                    }
                    n += 1;
                }
            }
            Message::Ping(p) => {
                let _ = write.send(Message::Pong(p)).await;
            }
            Message::Close(_) => break,
            _ => {}
        }
    }
    Ok(n)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_combined_trade() {
        let raw = r#"{"stream":"btcusdt@trade","data":{"e":"trade","E":1,"s":"BTCUSDT","t":99,"p":"64012.50","q":"0.010","T":2,"m":false}}"#;
        let t = parse_binance_trade_msg(raw).expect("tick");
        assert_eq!(t.symbol, "BTCUSDT");
        assert_eq!(t.venue, Venue::Binance);
        assert_eq!(t.side, Side::Buy);
        assert_eq!(t.price_i, price_i_from_str("64012.50").unwrap());
    }
}
