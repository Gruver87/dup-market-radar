//! Bybit spot publicTrade WS → Tick.

use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use futures_util::{SinkExt, StreamExt};
use radar_core::{price_i_from_str, qty_i_from_str, Side, Tick, Venue};
use serde_json::{json, Value};
use tokio::sync::mpsc;
use tokio::time::{sleep, timeout};
use tokio_tungstenite::{connect_async, tungstenite::Message};
use url::Url;

use crate::ws_time::{connect_budget, deadline_elapsed, session_ending, READ_IDLE};

const DEFAULT_WS: &str = "wss://stream.bybit.com/v5/public/spot";

#[derive(Debug, Clone)]
pub struct BybitIngestConfig {
    pub symbols: Vec<String>,
    pub ws_url: String,
    pub max_reconnects: u32,
}

impl Default for BybitIngestConfig {
    fn default() -> Self {
        Self {
            symbols: vec![
                "BTCUSDT".into(),
                "ETHUSDT".into(),
                "SOLUSDT".into(),
            ],
            ws_url: DEFAULT_WS.into(),
            max_reconnects: 8,
        }
    }
}

fn subscribe_args(symbols: &[String]) -> Vec<String> {
    symbols
        .iter()
        .map(|s| format!("publicTrade.{}", s.trim().to_ascii_uppercase()))
        .collect()
}

/// Parse Bybit v5 publicTrade push (single trade object or array under data).
pub fn parse_bybit_trade_msg(text: &str) -> Vec<Tick> {
    let v: Value = match serde_json::from_str(text) {
        Ok(v) => v,
        Err(_) => return vec![],
    };
    let topic = v.get("topic").and_then(|t| t.as_str()).unwrap_or("");
    if !topic.starts_with("publicTrade.") {
        return vec![];
    }
    let data = match v.get("data") {
        Some(d) => d,
        None => return vec![],
    };
    let rows: Vec<&Value> = if data.is_array() {
        data.as_array().map(|a| a.iter().collect()).unwrap_or_default()
    } else if data.is_object() {
        vec![data]
    } else {
        return vec![];
    };
    let mut out = Vec::with_capacity(rows.len());
    for row in rows {
        if let Some(t) = tick_from_row(row) {
            out.push(t);
        }
    }
    out
}

fn tick_from_row(row: &Value) -> Option<Tick> {
    let symbol = row
        .get("s")
        .and_then(|s| s.as_str())
        .map(|s| s.to_ascii_uppercase())?;
    let price = price_i_from_str(row.get("p")?.as_str()?)?;
    let qty = qty_i_from_str(row.get("v")?.as_str()?)?;
    let ts_ms = row.get("T").and_then(|t| t.as_i64())?;
    let side = match row.get("S").and_then(|s| s.as_str()) {
        Some("Buy") | Some("buy") => Side::Buy,
        Some("Sell") | Some("sell") => Side::Sell,
        _ => Side::Unknown,
    };
    let trade_id = row.get("i").map(|t| t.to_string().trim_matches('"').to_string());
    Some(Tick {
        venue: Venue::Bybit,
        symbol,
        price_i: price,
        qty_i: qty,
        ts_ms,
        side,
        trade_id,
    })
}

/// Connect, subscribe, forward ticks until `duration` elapses (or forever if zero).
pub async fn run_bybit_trades(
    cfg: BybitIngestConfig,
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
        if attempts > 0 && session_ending(deadline) {
            break;
        }
        attempts += 1;
        let url = Url::parse(&cfg.ws_url).context("ws url")?;
        match connect_and_pump(&url, &cfg.symbols, deadline, &tx).await {
            Ok(n) => {
                total += n;
                if n > 0 {
                    reconnects = 0;
                }
                if deadline_elapsed(deadline) || (n == 0 && !forever) {
                    break;
                }
            }
            Err(e) => {
                if deadline_elapsed(deadline) || session_ending(deadline) {
                    break;
                }
                reconnects += 1;
                if cfg.max_reconnects > 0 && reconnects > cfg.max_reconnects {
                    return Err(e).context("bybit ws reconnects exhausted");
                }
                eprintln!("radar_ingest bybit reconnect: {e:#}");
                sleep(Duration::from_millis(500 * u64::from(reconnects.min(10)))).await;
            }
        }
    }
    Ok(total)
}

async fn connect_and_pump(
    url: &Url,
    symbols: &[String],
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

    let sub = json!({
        "op": "subscribe",
        "args": subscribe_args(symbols),
    });
    write
        .send(Message::Text(sub.to_string()))
        .await
        .context("subscribe send")?;

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
                    break;
                }
                continue;
            }
        };
        match msg {
            Message::Text(text) => {
                for tick in parse_bybit_trade_msg(&text) {
                    if tx.send(tick).await.is_err() {
                        return Ok(n);
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
    fn parse_public_trade_array() {
        let raw = r#"{"topic":"publicTrade.BTCUSDT","type":"snapshot","ts":1,"data":[{"i":"9","T":2,"p":"64012.50","v":"0.010","S":"Buy","s":"BTCUSDT","BT":false}]}"#;
        let ticks = parse_bybit_trade_msg(raw);
        assert_eq!(ticks.len(), 1);
        let t = &ticks[0];
        assert_eq!(t.symbol, "BTCUSDT");
        assert_eq!(t.venue, Venue::Bybit);
        assert_eq!(t.side, Side::Buy);
        assert_eq!(t.price_i, price_i_from_str("64012.50").unwrap());
    }
}
