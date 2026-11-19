//! KuCoin spot match WS → Tick (bullet-public token + /market/match).

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

const BULLET_URL: &str = "https://api.kucoin.com/api/v1/bullet-public";

#[derive(Debug, Clone)]
pub struct KucoinIngestConfig {
    pub symbols: Vec<String>,
    pub bullet_url: String,
    pub max_reconnects: u32,
}

impl Default for KucoinIngestConfig {
    fn default() -> Self {
        Self {
            symbols: vec![
                "BTCUSDT".into(),
                "ETHUSDT".into(),
                "SOLUSDT".into(),
            ],
            bullet_url: BULLET_URL.into(),
            max_reconnects: 8,
        }
    }
}

/// BTCUSDT → BTC-USDT (KuCoin wire symbol).
pub fn to_kucoin_symbol(raw: &str) -> String {
    let s = raw.trim().to_ascii_uppercase().replace('-', "").replace('_', "");
    if s.contains('-') {
        return s;
    }
    for quote in ["USDT", "USDC", "BTC", "ETH", "KCS"] {
        if s.ends_with(quote) && s.len() > quote.len() {
            let base = &s[..s.len() - quote.len()];
            return format!("{base}-{quote}");
        }
    }
    s
}

fn match_topic(symbols: &[String]) -> String {
    let parts: Vec<String> = symbols.iter().map(|s| to_kucoin_symbol(s)).collect();
    format!("/market/match:{}", parts.join(","))
}

#[derive(Debug, Clone)]
struct BulletSession {
    endpoint: String,
    token: String,
    ping_interval_ms: u64,
}

async fn fetch_bullet(url: &str) -> Result<BulletSession> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(12))
        .build()
        .context("reqwest client")?;
    let resp: Value = client
        .post(url)
        .send()
        .await
        .context("bullet-public POST")?
        .error_for_status()
        .context("bullet-public status")?
        .json()
        .await
        .context("bullet-public json")?;
    let data = resp
        .get("data")
        .ok_or_else(|| anyhow!("bullet missing data"))?;
    let token = data
        .get("token")
        .and_then(|t| t.as_str())
        .ok_or_else(|| anyhow!("bullet missing token"))?
        .to_string();
    let servers = data
        .get("instanceServers")
        .and_then(|s| s.as_array())
        .ok_or_else(|| anyhow!("bullet missing instanceServers"))?;
    let server = servers
        .first()
        .ok_or_else(|| anyhow!("bullet empty instanceServers"))?;
    let endpoint = server
        .get("endpoint")
        .and_then(|e| e.as_str())
        .ok_or_else(|| anyhow!("bullet missing endpoint"))?
        .to_string();
    let ping_interval_ms = server
        .get("pingInterval")
        .and_then(|p| p.as_u64())
        .unwrap_or(18_000);
    Ok(BulletSession {
        endpoint,
        token,
        ping_interval_ms,
    })
}

/// Parse KuCoin match push into ticks (0..1).
pub fn parse_kucoin_match_msg(text: &str) -> Option<Tick> {
    let v: Value = serde_json::from_str(text).ok()?;
    if v.get("type").and_then(|t| t.as_str()) != Some("message") {
        return None;
    }
    let topic = v.get("topic").and_then(|t| t.as_str()).unwrap_or("");
    if !topic.starts_with("/market/match") {
        return None;
    }
    let data = v.get("data")?;
    let symbol_raw = data.get("symbol").and_then(|s| s.as_str())?;
    let symbol = symbol_raw.to_ascii_uppercase().replace('-', "");
    let price = price_i_from_str(data.get("price")?.as_str()?)?;
    let qty = qty_i_from_str(data.get("size")?.as_str()?)?;
    let side = match data.get("side").and_then(|s| s.as_str()) {
        Some("buy") | Some("Buy") => Side::Buy,
        Some("sell") | Some("Sell") => Side::Sell,
        _ => Side::Unknown,
    };
    // KuCoin time is often nanoseconds string
    let ts_ms = data
        .get("time")
        .and_then(|t| {
            if let Some(n) = t.as_u64() {
                Some(if n > 10_000_000_000_000 {
                    (n / 1_000_000) as i64
                } else if n > 10_000_000_000 {
                    (n / 1_000) as i64
                } else {
                    n as i64
                })
            } else if let Some(s) = t.as_str() {
                let n: u64 = s.parse().ok()?;
                Some(if n > 10_000_000_000_000 {
                    (n / 1_000_000) as i64
                } else if n > 10_000_000_000 {
                    (n / 1_000) as i64
                } else {
                    n as i64
                })
            } else {
                None
            }
        })
        .unwrap_or(0);
    let trade_id = data
        .get("tradeId")
        .map(|t| t.to_string().trim_matches('"').to_string());
    Some(Tick {
        venue: Venue::Kucoin,
        symbol,
        price_i: price,
        qty_i: qty,
        ts_ms,
        side,
        trade_id,
    })
}

pub async fn run_kucoin_trades(
    cfg: KucoinIngestConfig,
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
        match connect_and_pump(&cfg, deadline, &tx).await {
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
                    return Err(e).context("kucoin ws reconnects exhausted");
                }
                eprintln!("radar_ingest kucoin reconnect: {e:#}");
                sleep(Duration::from_millis(500 * u64::from(reconnects.min(10)))).await;
            }
        }
    }
    Ok(total)
}

async fn connect_and_pump(
    cfg: &KucoinIngestConfig,
    deadline: Option<tokio::time::Instant>,
    tx: &mpsc::Sender<Tick>,
) -> Result<u64> {
    // Bullet HTTP first — allow even with a short smoke window.
    if deadline_elapsed(deadline) {
        return Ok(0);
    }
    let bullet = fetch_bullet(&cfg.bullet_url).await?;
    let ws_url = format!("{}?token={}", bullet.endpoint, bullet.token);
    let url = Url::parse(&ws_url).context("ws url")?;

    // After bullet RTT, still try WS if any budget remains (was: 3s floor → 0 ticks on --seconds 4).
    let Some(budget) = connect_budget(deadline) else {
        return Ok(0);
    };
    let (ws, _) = timeout(budget, connect_async(url.as_str()))
        .await
        .context("connect timeout")?
        .context("connect_async")?;
    let (mut write, mut read) = ws.split();

    let sub = json!({
        "id": chrono_like_id(),
        "type": "subscribe",
        "topic": match_topic(&cfg.symbols),
        "privateChannel": false,
        "response": true,
    });
    write
        .send(Message::Text(sub.to_string()))
        .await
        .context("subscribe send")?;

    let mut n = 0u64;
    let mut last_ping = tokio::time::Instant::now();
    // KuCoin docs: use server pingInterval; floor 10s to avoid chatty reconnect loops.
    let ping_every = Duration::from_millis(bullet.ping_interval_ms.max(10_000));

    loop {
        let left = match deadline {
            Some(d) => {
                let rem = d.saturating_duration_since(tokio::time::Instant::now());
                if rem.is_zero() {
                    let _ = write.close().await;
                    break;
                }
                rem.min(Duration::from_secs(5).min(READ_IDLE))
            }
            None => Duration::from_secs(5),
        };
        if last_ping.elapsed() >= ping_every {
            let ping = json!({"id": chrono_like_id(), "type": "ping"});
            let _ = write.send(Message::Text(ping.to_string())).await;
            last_ping = tokio::time::Instant::now();
        }
        let msg = match timeout(left, read.next()).await {
            Ok(Some(Ok(m))) => m,
            Ok(Some(Err(e))) => return Err(e.into()),
            Ok(None) => break,
            Err(_) => continue,
        };
        match msg {
            Message::Text(text) => {
                // reply to server ping
                if let Ok(v) = serde_json::from_str::<Value>(&text) {
                    if v.get("type").and_then(|t| t.as_str()) == Some("ping") {
                        let pong = json!({
                            "id": v.get("id").cloned().unwrap_or(json!(chrono_like_id())),
                            "type": "pong",
                        });
                        let _ = write.send(Message::Text(pong.to_string())).await;
                        continue;
                    }
                }
                if let Some(tick) = parse_kucoin_match_msg(&text) {
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

fn chrono_like_id() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    ms.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn symbol_wire() {
        assert_eq!(to_kucoin_symbol("BTCUSDT"), "BTC-USDT");
        assert_eq!(to_kucoin_symbol("sol-usdt"), "SOL-USDT");
    }

    #[test]
    fn parse_match() {
        let raw = r#"{"type":"message","topic":"/market/match:BTC-USDT","subject":"trade.l3match","data":{"symbol":"BTC-USDT","side":"buy","price":"64012.50","size":"0.010","tradeId":"1","time":"1700000000123456789"}}"#;
        let t = parse_kucoin_match_msg(raw).expect("tick");
        assert_eq!(t.symbol, "BTCUSDT");
        assert_eq!(t.venue, Venue::Kucoin);
        assert_eq!(t.side, Side::Buy);
        assert_eq!(t.price_i, price_i_from_str("64012.50").unwrap());
        assert!(t.ts_ms > 0);
    }
}
