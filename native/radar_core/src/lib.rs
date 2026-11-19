//! Shared market types for DUP Market Radar native engine.
//! No pyo3 — safe for ingest daemons and score crates.

use serde::{Deserialize, Serialize};

/// Radar price scale: 1.0 USDT → 100_000_000.
pub const PRICE_SCALE: i64 = 100_000_000;
/// USDT notional micros: 1.0 USDT → 1_000_000.
pub const QUOTE_SCALE: i64 = 1_000_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Venue {
    Binance,
    Bybit,
    Kucoin,
    Mexc,
    Gate,
    Okx,
    Bitget,
    Htx,
    Unknown,
}

impl Venue {
    pub fn as_str(self) -> &'static str {
        match self {
            Venue::Binance => "binance",
            Venue::Bybit => "bybit",
            Venue::Kucoin => "kucoin",
            Venue::Mexc => "mexc",
            Venue::Gate => "gate",
            Venue::Okx => "okx",
            Venue::Bitget => "bitget",
            Venue::Htx => "htx",
            Venue::Unknown => "unknown",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Side {
    Buy,
    Sell,
    Unknown,
}

/// Normalized trade/tick — integer money only.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Tick {
    pub venue: Venue,
    pub symbol: String,
    /// Price × PRICE_SCALE
    pub price_i: i64,
    /// Base qty × PRICE_SCALE (same fixed point as price for simplicity on tiny sizes)
    pub qty_i: i64,
    /// Exchange event time (ms)
    pub ts_ms: i64,
    pub side: Side,
    #[serde(default)]
    pub trade_id: Option<String>,
}

impl Tick {
    pub fn notional_quote_i(&self) -> Option<i64> {
        // price_i/PRICE_SCALE * qty_i/PRICE_SCALE * QUOTE_SCALE
        // = price_i * qty_i * QUOTE_SCALE / PRICE_SCALE^2
        let num = (self.price_i as i128)
            .checked_mul(self.qty_i as i128)?
            .checked_mul(QUOTE_SCALE as i128)?;
        let den = (PRICE_SCALE as i128).checked_mul(PRICE_SCALE as i128)?;
        let v = num / den;
        if v > i64::MAX as i128 || v < i64::MIN as i128 {
            return None;
        }
        Some(v as i64)
    }
}

/// Parse a decimal string ("64012.51") into fixed-point i64 at `scale`.
pub fn parse_decimal_i(raw: &str, scale: i64) -> Option<i64> {
    let s = raw.trim();
    if s.is_empty() {
        return None;
    }
    let neg = s.starts_with('-');
    let s = if neg || s.starts_with('+') {
        &s[1..]
    } else {
        s
    };
    let (whole, frac) = match s.split_once('.') {
        Some((w, f)) => (w, f),
        None => (s, ""),
    };
    if whole.is_empty() && frac.is_empty() {
        return None;
    }
    let whole_n: i64 = if whole.is_empty() {
        0
    } else {
        whole.parse().ok()?
    };
    let mut frac_digits = 0_i64;
    let mut place = scale / 10;
    for ch in frac.chars() {
        if !ch.is_ascii_digit() || place == 0 {
            break;
        }
        let d = (ch as u8 - b'0') as i64;
        frac_digits += d * place;
        place /= 10;
    }
    let mut v = whole_n
        .checked_mul(scale)?
        .checked_add(frac_digits)?;
    if neg {
        v = -v;
    }
    Some(v)
}

pub fn price_i_from_str(raw: &str) -> Option<i64> {
    parse_decimal_i(raw, PRICE_SCALE)
}

pub fn qty_i_from_str(raw: &str) -> Option<i64> {
    parse_decimal_i(raw, PRICE_SCALE)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_btc_price() {
        let p = price_i_from_str("64012.51").unwrap();
        assert_eq!(p, 6_401_251_000_000); // 64012.51 * 1e8
    }

    #[test]
    fn parse_small_qty() {
        let q = qty_i_from_str("0.001").unwrap();
        assert_eq!(q, 100_000); // 0.001 * 1e8
    }

    #[test]
    fn tick_notional() {
        let t = Tick {
            venue: Venue::Binance,
            symbol: "BTCUSDT".into(),
            price_i: 50_000 * PRICE_SCALE,
            qty_i: PRICE_SCALE / 1000, // 0.001
            ts_ms: 1,
            side: Side::Buy,
            trade_id: None,
        };
        // 50_000 * 0.001 = 50 USDT → 50e6 micros
        assert_eq!(t.notional_quote_i(), Some(50 * QUOTE_SCALE));
    }
}
