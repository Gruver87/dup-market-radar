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

/// OHLCV bar — integer money; foundation for MTF in Rust.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Bar {
    pub venue: Venue,
    pub symbol: String,
    /// Bar open time (ms), aligned to interval
    pub open_ms: i64,
    pub open_i: i64,
    pub high_i: i64,
    pub low_i: i64,
    pub close_i: i64,
    pub volume_i: i64,
    /// Interval seconds (60 = 1m, 900 = 15m, …)
    pub interval_sec: u32,
    pub trade_count: u32,
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

/// Align exchange ts_ms down to interval open.
pub fn bar_open_ms(ts_ms: i64, interval_sec: u32) -> Option<i64> {
    if interval_sec == 0 || ts_ms < 0 {
        return None;
    }
    let step = (interval_sec as i64).checked_mul(1000)?;
    Some(ts_ms - (ts_ms.rem_euclid(step)))
}

/// (to - from) / from in basis points × 1 (integer bps).
pub fn change_bps(from_i: i64, to_i: i64) -> Option<i64> {
    if from_i <= 0 {
        return None;
    }
    let num = (to_i as i128 - from_i as i128).checked_mul(10_000)?;
    let v = num / (from_i as i128);
    if v > i64::MAX as i128 || v < i64::MIN as i128 {
        return None;
    }
    Some(v as i64)
}

/// Fold ticks into fixed-interval bars; emits closed bars on rollover.
#[derive(Debug, Default)]
pub struct BarAggregator {
    interval_sec: u32,
    current: Option<Bar>,
}

impl BarAggregator {
    pub fn new(interval_sec: u32) -> Self {
        Self {
            interval_sec: interval_sec.max(1),
            current: None,
        }
    }

    pub fn interval_sec(&self) -> u32 {
        self.interval_sec
    }

    /// Push a tick. Returns a **closed** bar when the interval rolls.
    pub fn push(&mut self, tick: &Tick) -> Option<Bar> {
        let open = bar_open_ms(tick.ts_ms, self.interval_sec)?;
        match self.current.as_mut() {
            None => {
                self.current = Some(Bar {
                    venue: tick.venue,
                    symbol: tick.symbol.clone(),
                    open_ms: open,
                    open_i: tick.price_i,
                    high_i: tick.price_i,
                    low_i: tick.price_i,
                    close_i: tick.price_i,
                    volume_i: tick.qty_i.max(0),
                    interval_sec: self.interval_sec,
                    trade_count: 1,
                });
                None
            }
            Some(bar) if bar.open_ms == open && bar.symbol == tick.symbol => {
                if tick.price_i > bar.high_i {
                    bar.high_i = tick.price_i;
                }
                if tick.price_i < bar.low_i {
                    bar.low_i = tick.price_i;
                }
                bar.close_i = tick.price_i;
                bar.volume_i = bar.volume_i.saturating_add(tick.qty_i.max(0));
                bar.trade_count = bar.trade_count.saturating_add(1);
                None
            }
            Some(_) => {
                let closed = self.current.take();
                self.current = Some(Bar {
                    venue: tick.venue,
                    symbol: tick.symbol.clone(),
                    open_ms: open,
                    open_i: tick.price_i,
                    high_i: tick.price_i,
                    low_i: tick.price_i,
                    close_i: tick.price_i,
                    volume_i: tick.qty_i.max(0),
                    interval_sec: self.interval_sec,
                    trade_count: 1,
                });
                closed
            }
        }
    }

    /// Emit in-progress bar (partial / end of stream).
    pub fn flush(&mut self) -> Option<Bar> {
        self.current.take()
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

    #[test]
    fn bar_rollup_1m() {
        let mut agg = BarAggregator::new(60);
        let base = 1_700_000_000_000i64; // aligned-ish
        let open0 = bar_open_ms(base, 60).unwrap();
        let t1 = Tick {
            venue: Venue::Binance,
            symbol: "BTCUSDT".into(),
            price_i: 100 * PRICE_SCALE,
            qty_i: PRICE_SCALE,
            ts_ms: open0 + 1_000,
            side: Side::Buy,
            trade_id: None,
        };
        let t2 = Tick {
            venue: Venue::Binance,
            symbol: "BTCUSDT".into(),
            price_i: 110 * PRICE_SCALE,
            qty_i: PRICE_SCALE,
            ts_ms: open0 + 2_000,
            side: Side::Buy,
            trade_id: None,
        };
        assert!(agg.push(&t1).is_none());
        assert!(agg.push(&t2).is_none());
        let t3 = Tick {
            venue: Venue::Binance,
            symbol: "BTCUSDT".into(),
            price_i: 105 * PRICE_SCALE,
            qty_i: PRICE_SCALE,
            ts_ms: open0 + 60_000 + 500,
            side: Side::Sell,
            trade_id: None,
        };
        let closed = agg.push(&t3).expect("closed bar");
        assert_eq!(closed.open_i, 100 * PRICE_SCALE);
        assert_eq!(closed.high_i, 110 * PRICE_SCALE);
        assert_eq!(closed.close_i, 110 * PRICE_SCALE);
        assert_eq!(closed.trade_count, 2);
        assert_eq!(change_bps(closed.open_i, closed.close_i), Some(1_000)); // +10%
        let partial = agg.flush().expect("partial");
        assert_eq!(partial.open_i, 105 * PRICE_SCALE);
        assert_eq!(partial.trade_count, 1);
    }
}
