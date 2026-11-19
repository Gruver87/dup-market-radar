//! Live MEXC spot aggre deals → NDJSON ticks on stdout.
//!
//! ```text
//! cargo run -p radar_ingest --bin radar-ingest-mexc -- --seconds 8
//! ```

use std::time::Duration;

use clap::Parser;
use radar_ingest::{run_mexc_trades, MexcIngestConfig};
use tokio::sync::mpsc;

#[derive(Parser, Debug)]
#[command(name = "radar-ingest-mexc")]
#[command(about = "DUP Radar — MEXC trade WS → NDJSON ticks (watch only)")]
struct Args {
    /// How long to listen (0 = until Ctrl+C / channel close)
    #[arg(long, default_value_t = 8)]
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
    let cfg = MexcIngestConfig {
        symbols,
        ..MexcIngestConfig::default()
    };

    let (tx, mut rx) = mpsc::channel(4096);
    let duration = Duration::from_secs(args.seconds);
    let ingest = tokio::spawn(async move { run_mexc_trades(cfg, duration, tx).await });

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
            "radar-ingest-mexc done printed={printed} ws_ticks≈{total} window_s={}",
            args.seconds
        );
    }
    if printed == 0 {
        anyhow::bail!("no ticks received — check network / MEXC WS");
    }
    Ok(())
}
