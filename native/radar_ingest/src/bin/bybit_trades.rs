//! Live Bybit spot trades → NDJSON ticks on stdout.
//!
//! ```text
//! cargo run -p radar_ingest --bin radar-ingest-bybit -- --seconds 5
//! ```

use std::time::Duration;

use clap::Parser;
use radar_ingest::{run_bybit_trades, BybitIngestConfig};
use tokio::sync::mpsc;

#[derive(Parser, Debug)]
#[command(name = "radar-ingest-bybit")]
#[command(about = "DUP Radar — Bybit trade WS → NDJSON ticks (watch only)")]
struct Args {
    /// How long to listen (0 = until Ctrl+C / channel close)
    #[arg(long, default_value_t = 5)]
    seconds: u64,

    /// Comma-separated symbols
    #[arg(long, default_value = "BTCUSDT,ETHUSDT,SOLUSDT")]
    symbols: String,

    /// Print at most N ticks (0 = unlimited within window)
    #[arg(long, default_value_t = 0)]
    max_ticks: u64,

    /// Summary line to stderr at end
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
    let cfg = BybitIngestConfig {
        symbols,
        ..BybitIngestConfig::default()
    };

    let (tx, mut rx) = mpsc::channel(4096);
    let duration = Duration::from_secs(args.seconds);
    let ingest = tokio::spawn(async move { run_bybit_trades(cfg, duration, tx).await });

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
            "radar-ingest-bybit done printed={printed} ws_ticks≈{total} window_s={}",
            args.seconds
        );
    }
    if printed == 0 {
        anyhow::bail!("no ticks received — check network / Bybit WS");
    }
    Ok(())
}
