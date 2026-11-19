//! MEXC spot aggregated deals WS → Tick (protobuf push + JSON fallback).

use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use futures_util::{SinkExt, StreamExt};
use prost::Message as ProstMessage;
use radar_core::{price_i_from_str, qty_i_from_str, Side, Tick, Venue};
use serde_json::{json, Value};
use tokio::sync::mpsc;
use tokio::time::{sleep, timeout};
use tokio_tungstenite::{connect_async, tungstenite::Message};
use url::Url;

use crate::ws_time::{connect_budget, deadline_elapsed, session_ending, READ_IDLE};

pub mod pb {
    include!(concat!(env!("OUT_DIR"), "/mexc.rs"));
}

const DEFAULT_WS: &str = "wss://wbs-api.mexc.com/ws";

#[derive(Debug, Clone)]
pub struct MexcIngestConfig {
    pub symbols: Vec<String>,
    pub ws_url: String,
    pub max_reconnects: u32,
}

impl Default for MexcIngestConfig {
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

fn subscribe_params(symbols: &[String]) -> Vec<String> {
    symbols
        .iter()
        .map(|s| {
            format!(
                "spot@public.aggre.deals.v3.api.pb@100ms@{}",
                s.trim().to_ascii_uppercase()
            )
        })
        .collect()
}

fn side_from_trade_type(t: i32) -> Side {
    match t {
        1 => Side::Buy,
        2 => Side::Sell,
        _ => Side::Unknown,
    }
}

fn tick_from_deal(
    symbol: &str,
    price: &str,
    qty: &str,
    trade_type: i32,
    time: i64,
    trade_id: Option<String>,
) -> Option<Tick> {
    let price_i = price_i_from_str(price)?;
    let qty_i = qty_i_from_str(qty)?;
    if time <= 0 {
        return None;
    }
    Some(Tick {
        venue: Venue::Mexc,
        symbol: symbol.to_ascii_uppercase(),
        price_i,
        qty_i,
        ts_ms: time,
        side: side_from_trade_type(trade_type),
        trade_id,
    })
}

/// Parse official JSON sample / text frames (if gateway emits JSON).
pub fn parse_mexc_deals_json(text: &str) -> Vec<Tick> {
    let v: Value = match serde_json::from_str(text) {
        Ok(v) => v,
        Err(_) => return vec![],
    };
    let symbol = v
        .get("symbol")
        .and_then(|s| s.as_str())
        .unwrap_or("")
        .to_ascii_uppercase();
    if symbol.is_empty() {
        return vec![];
    }
    let deals = v
        .pointer("/publicAggreDeals/deals")
        .or_else(|| v.pointer("/publicdeals/dealsList"))
        .and_then(|d| d.as_array())
        .cloned()
        .unwrap_or_default();
    let mut out = Vec::with_capacity(deals.len());
    for row in deals {
        let price = row
            .get("price")
            .and_then(|p| p.as_str())
            .unwrap_or("");
        let qty = row
            .get("quantity")
            .and_then(|q| q.as_str())
            .unwrap_or("");
        let trade_type = row
            .get("tradeType")
            .or_else(|| row.get("tradetype"))
            .and_then(|t| t.as_i64())
            .unwrap_or(0) as i32;
        let time = row
            .get("time")
            .and_then(|t| t.as_i64().or_else(|| t.as_str().and_then(|s| s.parse().ok())))
            .unwrap_or(0);
        let trade_id = row
            .get("tradeId")
            .or_else(|| row.get("tradeid"))
            .map(|t| t.to_string().trim_matches('"').to_string());
        if let Some(tick) = tick_from_deal(&symbol, price, qty, trade_type, time, trade_id) {
            out.push(tick);
        }
    }
    out
}

/// Parse protobuf push bytes (primary MEXC path).
pub fn parse_mexc_deals_pb(bytes: &[u8]) -> Vec<Tick> {
    let wrap = match pb::PushDataV3ApiWrapper::decode(bytes) {
        Ok(w) => w,
        Err(_) => return vec![],
    };
    let symbol = wrap
        .symbol
        .clone()
        .unwrap_or_default()
        .to_ascii_uppercase();
    let Some(pb::push_data_v3_api_wrapper::Body::PublicAggreDeals(deals)) = wrap.body else {
        return vec![];
    };
    if symbol.is_empty() {
        return vec![];
    }
    let mut out = Vec::with_capacity(deals.deals.len());
    for d in deals.deals {
        if let Some(tick) = tick_from_deal(
            &symbol,
            &d.price,
            &d.quantity,
            d.trade_type,
            d.time,
            if d.trade_id.is_empty() {
                None
            } else {
                Some(d.trade_id)
            },
        ) {
            out.push(tick);
        }
    }
    out
}

pub async fn run_mexc_trades(
    cfg: MexcIngestConfig,
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
                    return Err(e).context("mexc ws reconnects exhausted");
                }
                eprintln!("radar_ingest mexc reconnect: {e:#}");
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
        "method": "SUBSCRIPTION",
        "params": subscribe_params(symbols),
    });
    write
        .send(Message::Text(sub.to_string()))
        .await
        .context("subscribe send")?;

    let mut n = 0u64;
    let mut last_ping = tokio::time::Instant::now();
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
        if last_ping.elapsed() >= Duration::from_secs(20) {
            let _ = write
                .send(Message::Text(r#"{"method":"PING"}"#.into()))
                .await;
            last_ping = tokio::time::Instant::now();
        }
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
                for tick in parse_mexc_deals_json(&text) {
                    if tx.send(tick).await.is_err() {
                        return Ok(n);
                    }
                    n += 1;
                }
            }
            Message::Binary(bin) => {
                for tick in parse_mexc_deals_pb(&bin) {
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
    fn parse_json_aggre_deals() {
        let raw = r#"{
          "channel":"spot@public.aggre.deals.v3.api.pb@100ms@BTCUSDT",
          "symbol":"BTCUSDT",
          "publicAggreDeals":{
            "deals":[{
              "price":"64012.50",
              "quantity":"0.010",
              "tradeType":1,
              "time":"1700000000000",
              "tradeId":"abc"
            }]
          }
        }"#;
        let ticks = parse_mexc_deals_json(raw);
        assert_eq!(ticks.len(), 1);
        let t = &ticks[0];
        assert_eq!(t.venue, Venue::Mexc);
        assert_eq!(t.symbol, "BTCUSDT");
        assert_eq!(t.side, Side::Buy);
        assert_eq!(t.price_i, price_i_from_str("64012.50").unwrap());
    }

    #[test]
    fn parse_pb_roundtrip() {
        let wrap = pb::PushDataV3ApiWrapper {
            channel: "spot@public.aggre.deals.v3.api.pb@100ms@ETHUSDT".into(),
            body: Some(pb::push_data_v3_api_wrapper::Body::PublicAggreDeals(
                pb::PublicAggreDealsV3Api {
                    deals: vec![pb::PublicAggreDealsV3ApiItem {
                        price: "3000.00".into(),
                        quantity: "0.5".into(),
                        trade_type: 2,
                        time: 1_700_000_000_000,
                        trade_id: "x1".into(),
                    }],
                    event_type: "spot@public.aggre.deals.v3.api.pb@100ms".into(),
                },
            )),
            symbol: Some("ETHUSDT".into()),
            symbol_id: None,
            create_time: None,
            send_time: Some(1_700_000_000_001),
        };
        let mut buf = Vec::new();
        wrap.encode(&mut buf).unwrap();
        let ticks = parse_mexc_deals_pb(&buf);
        assert_eq!(ticks.len(), 1);
        assert_eq!(ticks[0].side, Side::Sell);
        assert_eq!(ticks[0].symbol, "ETHUSDT");
    }
}
