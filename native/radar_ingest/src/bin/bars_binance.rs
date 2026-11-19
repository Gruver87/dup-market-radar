//! Live Binance trades → closed OHLCV bars (NDJSON). Watch only.
//!
//! Short smoke: `--interval-sec 5 --seconds 12`

use std::collections::HashMap;
use std::time::Duration;

use clap::Parser;
use radar_core::BarAggregator;
use radar_ingest::{run_binance_trades, BinanceIngestConfig};
use tokio::sync::mpsc;

#[derive(Parser, Debug)]
#[command(name = "radar-ingest-bars")]
#[command(about = "DUP Radar — Binance trades → OHLCV bars (watch only)")]
struct Args {
    #[arg(long, default_value_t = 12)]
    seconds: u64,

    #[arg(long, default_value = "BTCUSDT,ETHUSDT")]
    symbols: String,

    /// Bar interval seconds (use 5 for short smoke)
    #[arg(long, default_value_t = 5)]
    interval_sec: u32,

    #[arg(long, default_value_t = 0)]
    max_bars: u64,

    #[arg(long, default_value_t = true)]
    summary: bool,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args = Args::parse();
    let symbols: Vec<String> = args
        .symbols
        .split(',')
        .map(|s| s.trim().to_ascii_uppercase())
        .filter(|s| !s.is_empty())
        .collect();
    let cfg = BinanceIngestConfig {
        symbols: symbols.clone(),
        ..BinanceIngestConfig::default()
    };

    let (tx, mut rx) = mpsc::channel(8192);
    let duration = Duration::from_secs(args.seconds.max(1));
    let ingest = tokio::spawn(async move { run_binance_trades(cfg, duration, tx).await });

    let mut aggs: HashMap<String, BarAggregator> = HashMap::new();
    for s in &symbols {
        aggs.insert(s.clone(), BarAggregator::new(args.interval_sec.max(1)));
    }

    let mut printed = 0u64;
    let mut ticks = 0u64;
    while let Some(tick) = rx.recv().await {
        ticks += 1;
        let agg = aggs
            .entry(tick.symbol.clone())
            .or_insert_with(|| BarAggregator::new(args.interval_sec.max(1)));
        if let Some(bar) = agg.push(&tick) {
            println!("{}", serde_json::to_string(&bar)?);
            printed += 1;
            if args.max_bars > 0 && printed >= args.max_bars {
                break;
            }
        }
    }
    drop(rx);

    // Flush partials so short windows still show something honest.
    for (_sym, mut agg) in aggs {
        if let Some(bar) = agg.flush() {
            let mut v = serde_json::to_value(&bar)?;
            if let Some(obj) = v.as_object_mut() {
                obj.insert("partial".into(), serde_json::Value::Bool(true));
            }
            println!("{}", serde_json::to_string(&v)?);
            printed += 1;
        }
    }

    let total = ingest.await??;
    if args.summary {
        eprintln!(
            "radar-ingest-bars done bars_printed={printed} ticks≈{ticks} ws_ticks≈{total} interval_s={} window_s={}",
            args.interval_sec, args.seconds
        );
    }
    if printed == 0 {
        anyhow::bail!("no bars — check network / Binance WS");
    }
    Ok(())
}
