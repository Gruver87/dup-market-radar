//! Shared WS connect budget vs session deadline.
//!
//! Short smoke windows used to log false "reconnect: connect timeout" when only
//! a few hundred ms remained for TLS. KuCoin also needs bullet-public RTT first —
//! a 3s skip-floor ate the whole `--seconds 4` window after HTTP.

use std::time::Duration;

use tokio::time::Instant;

/// Hard cap for a single TCP+TLS+WS handshake.
pub const CONNECT_TIMEOUT: Duration = Duration::from_secs(20);
/// Skip *starting* a handshake only when almost nothing remains.
pub const CONNECT_SKIP_FLOOR: Duration = Duration::from_millis(400);
/// Don't start another reconnect cycle below this (real drops still retry above).
pub const RECONNECT_SKIP_FLOOR: Duration = Duration::from_secs(2);
/// Idle read wait while pumping (Binance pings ~20s; leave headroom).
pub const READ_IDLE: Duration = Duration::from_secs(60);

/// `None` → do not attempt connect (session ending). `Some(d)` → handshake budget.
pub fn connect_budget(deadline: Option<Instant>) -> Option<Duration> {
    match deadline {
        None => Some(CONNECT_TIMEOUT),
        Some(d) => {
            let rem = d.saturating_duration_since(Instant::now());
            if rem < CONNECT_SKIP_FLOOR {
                None
            } else {
                Some(rem.min(CONNECT_TIMEOUT))
            }
        }
    }
}

/// True when a *reconnect* would be wasteful (session nearly over).
pub fn session_ending(deadline: Option<Instant>) -> bool {
    match deadline {
        None => false,
        Some(d) => d.saturating_duration_since(Instant::now()) < RECONNECT_SKIP_FLOOR,
    }
}

/// First attempt / mid-session: only stop when deadline fully elapsed.
pub fn deadline_elapsed(deadline: Option<Instant>) -> bool {
    match deadline {
        None => false,
        Some(d) => Instant::now() >= d,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn forever_gets_full_budget() {
        assert_eq!(connect_budget(None), Some(CONNECT_TIMEOUT));
    }

    #[test]
    fn tiny_remaining_skips_connect() {
        let d = Instant::now() + Duration::from_millis(100);
        assert!(connect_budget(Some(d)).is_none());
    }

    #[test]
    fn two_seconds_still_allows_handshake() {
        let d = Instant::now() + Duration::from_secs(2);
        let b = connect_budget(Some(d)).expect("budget");
        assert!(b >= Duration::from_millis(1500));
    }

    #[test]
    fn reconnect_skip_above_connect_skip() {
        let d = Instant::now() + Duration::from_millis(800);
        // Can still handshake...
        assert!(connect_budget(Some(d)).is_some());
        // ...but should not open a *new* reconnect cycle.
        assert!(session_ending(Some(d)));
    }

    #[test]
    fn healthy_remaining_capped() {
        let d = Instant::now() + Duration::from_secs(120);
        assert_eq!(connect_budget(Some(d)), Some(CONNECT_TIMEOUT));
    }
}
