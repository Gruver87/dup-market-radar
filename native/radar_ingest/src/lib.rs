//! Venue ingest adapters → [`radar_core::Tick`].

pub mod binance;
pub mod bybit;
pub mod kucoin;
pub mod ws_time;

pub use binance::{parse_binance_trade_msg, run_binance_trades, BinanceIngestConfig};
pub use bybit::{parse_bybit_trade_msg, run_bybit_trades, BybitIngestConfig};
pub use kucoin::{parse_kucoin_match_msg, run_kucoin_trades, to_kucoin_symbol, KucoinIngestConfig};
