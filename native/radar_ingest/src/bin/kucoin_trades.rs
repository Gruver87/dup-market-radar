//! Live KuCoin spot match trades → NDJSON ticks on stdout.

use std::time::Duration;

use clap::Parser;
use radar_ingest::{run_kucoin_trades, KucoinIngestConfig};
use tokio::sync::mpsc;

#[derive(Parser, Debug)]
#[command(name = "radar-ingest-kucoin")]
#[command(about = "DUP Radar — KuCoin match WS → NDJSON ticks (watch only)")]
struct Args {
    /// How long to listen (0 = until Ctrl+C / channel close)
    #[arg(long, default_value_t = 5)]
    seconds: u64,

    /// Comma-separated symbols (BTCUSDT or BTC-USDT)
    #[arg(long, default_value = "BTCUSDT,ETHUSDT,SOLUSDT")]
    symbols: String,

    #[arg(long, default_value_t = 0)]
    max_ticks: u64,

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
    let cfg = KucoinIngestConfig {
        symbols,
        ..KucoinIngestConfig::default()
    };

    let (tx, mut rx) = mpsc::channel(4096);
    let duration = Duration::from_secs(args.seconds);
    let ingest = tokio::spawn(async move { run_kucoin_trades(cfg, duration, tx).await });

    let mut printed = 0u64;
    while let Some(tick) = rx.recv().await {
        println!("{}", serde_json::to_string(&tick)?);
        printed += 1;
        if args.max_ticks > 0 && printed >= args.max_ticks {
            break;
        }
    }
    drop(rx);

    let total = ingest.await??;
    if args.summary {
        eprintln!(
            "radar-ingest-kucoin done printed={printed} ws_ticks≈{total} window_s={}",
            args.seconds
        );
    }
    if printed == 0 {
        anyhow::bail!("no ticks received — check network / KuCoin WS");
    }
    Ok(())
}
