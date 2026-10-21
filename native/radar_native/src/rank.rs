use serde_json::{json, Map, Value};

pub const PRICE_SCALE: i64 = 100_000_000;
pub const QUOTE_SCALE: i64 = 1_000_000;
pub const SCORE_CENTI: i64 = 100;

pub fn mul_div(a: i64, b: i64, denom: i64) -> i64 {
    if denom == 0 {
        return 0;
    }
    (a as i128 * b as i128 / denom as i128) as i64
}

pub fn clamp_i(x: i64, lo: i64, hi: i64) -> i64 {
    x.max(lo).min(hi)
}

pub fn as_i64(v: Option<&Value>) -> Option<i64> {
    v.and_then(|x| {
        x.as_i64()
            .or_else(|| x.as_u64().map(|u| u as i64))
            .or_else(|| x.as_f64().map(|f| f.floor() as i64))
    })
}

/// Config USDT amount (whole or float) → quote micros. Prefer `*_micros` / int keys upstream.
pub fn cfg_usdt_micros(v: Option<&Value>, default_usdt: i64) -> i64 {
    if let Some(i) = v.and_then(|x| x.as_i64()) {
        return i.saturating_mul(QUOTE_SCALE);
    }
    if let Some(u) = v.and_then(|x| x.as_u64()) {
        return (u as i64).saturating_mul(QUOTE_SCALE);
    }
    if let Some(f) = v.and_then(|x| x.as_f64()) {
        return (f * QUOTE_SCALE as f64).floor() as i64;
    }
    default_usdt.saturating_mul(QUOTE_SCALE)
}

pub fn cfg_price_i(v: Option<&Value>, default: i64) -> i64 {
    if let Some(i) = v.and_then(|x| x.as_i64()).filter(|&i| i > 1000) {
        // already scaled if large
        return i;
    }
    if let Some(f) = v.and_then(|x| x.as_f64()) {
        return (f * PRICE_SCALE as f64).floor() as i64;
    }
    if let Some(s) = v.and_then(|x| x.as_str()) {
        return parse_scaled(s, PRICE_SCALE).unwrap_or(default);
    }
    default
}

pub fn cfg_pct_bps(v: Option<&Value>, default_bps: i64) -> i64 {
    if let Some(i) = v.and_then(|x| x.as_i64()) {
        // if looks like already bps (>=1 and no fraction), treat as bps when key is *_bps
        return i;
    }
    if let Some(f) = v.and_then(|x| x.as_f64()) {
        return (f * 100.0).floor() as i64;
    }
    default_bps
}

pub fn cfg_funding_i(v: Option<&Value>, default: i64) -> i64 {
    if let Some(i) = v.and_then(|x| x.as_i64()).filter(|&i| i.abs() >= 100) {
        return i; // already 1e8-scaled
    }
    if let Some(f) = v.and_then(|x| x.as_f64()) {
        return (f * PRICE_SCALE as f64).floor() as i64;
    }
    default
}

fn parse_scaled(s: &str, scale: i64) -> Option<i64> {
    let s = s.trim();
    let neg = s.starts_with('-');
    let s = s.trim_start_matches('-');
    let digits = scale.to_string().len() - 1;
    let (whole, frac) = match s.split_once('.') {
        Some((w, f)) => (w, f),
        None => (s, ""),
    };
    if !whole.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    if !frac.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    let mut frac_pad = frac.chars().take(digits).collect::<String>();
    while frac_pad.len() < digits {
        frac_pad.push('0');
    }
    let n: i64 = format!("{whole}{frac_pad}").parse().ok()?;
    Some(if neg { -n } else { n })
}

pub fn price_str(n: i64) -> String {
    let neg = n < 0;
    let n = n.abs();
    let whole = n / PRICE_SCALE;
    let frac = n % PRICE_SCALE;
    let mut s = if frac == 0 {
        whole.to_string()
    } else {
        let mut f = format!("{:08}", frac);
        while f.ends_with('0') {
            f.pop();
        }
        format!("{whole}.{f}")
    };
    if neg && s != "0" {
        s.insert(0, '-');
    }
    s
}

pub fn log_volume_score_i(vol_i: Option<i64>, floor_i: i64) -> i64 {
    let Some(vol_i) = vol_i else {
        return 0;
    };
    if vol_i <= 0 || floor_i <= 0 {
        return 0;
    }
    if vol_i < floor_i {
        return clamp_i(mul_div(20 * SCORE_CENTI, vol_i, floor_i), 0, 100 * SCORE_CENTI);
    }
    let ratio = mul_div(vol_i, 1_000_000, floor_i);
    let log10_centi = log10_centi(ratio) - 600;
    clamp_i(
        40 * SCORE_CENTI + mul_div(15 * SCORE_CENTI, log10_centi, 100),
        0,
        100 * SCORE_CENTI,
    )
}

pub fn vol_relative_score_i(vs_median_bps: i64) -> i64 {
    clamp_i(50 * SCORE_CENTI + vs_median_bps / 4, 0, 100 * SCORE_CENTI)
}

pub fn blend_volume_score_i(vol_i: Option<i64>, floor_i: i64, vs_median_bps: Option<i64>) -> i64 {
    let abs_s = log_volume_score_i(vol_i, floor_i);
    let Some(vs) = vs_median_bps else {
        return abs_s;
    };
    let rel_s = vol_relative_score_i(vs);
    clamp_i((abs_s * 55 + rel_s * 45) / 100, 0, 100 * SCORE_CENTI)
}

fn log10_centi(x: i64) -> i64 {
    if x <= 0 {
        return 0;
    }
    let mut n = 0i64;
    let mut v = x;
    while v >= 10 {
        v /= 10;
        n += 1;
    }
    let mut lead = x;
    while lead >= 100 {
        lead /= 10;
    }
    let frac = mul_div(lead - 10, 100, 90);
    n * 100 + frac
}

pub fn spread_bps_i(row: &Value) -> Option<i64> {
    let bid = as_i64(row.get("bid_i"))?;
    let ask = as_i64(row.get("ask_i"))?;
    let price = as_i64(row.get("price_i"))?;
    if price > 0 && ask >= bid {
        Some(mul_div(ask - bid, 10_000, price))
    } else {
        None
    }
}

pub fn rank_top(mut candidates: Vec<Value>, n: usize) -> Vec<Value> {
    candidates.sort_by(|a, b| {
        let sa = as_i64(a.get("score_centi")).unwrap_or(0);
        let sb = as_i64(b.get("score_centi")).unwrap_or(0);
        sb.cmp(&sa)
    });
    let mut seen = std::collections::HashSet::new();
    let mut out = Vec::new();
    for c in candidates {
        let venue = c
            .get("venue")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let symbol = c
            .get("symbol")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let setup = c
            .get("setup")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let key = format!("{venue}|{symbol}|{setup}");
        if !seen.insert(key) {
            continue;
        }
        out.push(c);
        if out.len() >= n {
            break;
        }
    }
    out
}

pub fn candidate(
    sector: &str,
    row: &Value,
    score_centi: i64,
    setup: &str,
    entry_hint: &str,
    risk_flags: Vec<String>,
    metrics: Map<String, Value>,
) -> Value {
    let qv = row
        .get("quote_volume_i")
        .cloned()
        .or_else(|| row.get("liquidity_i").cloned())
        .unwrap_or(Value::Null);
    json!({
        "sector": sector,
        "symbol": row.get("symbol").cloned().unwrap_or(Value::Null),
        "venue": row.get("venue").cloned().unwrap_or(Value::Null),
        "price_i": row.get("price_i").cloned().unwrap_or(Value::Null),
        "change_bps": row.get("change_bps").cloned().unwrap_or(Value::Null),
        "quote_volume_i": qv,
        "score_centi": score_centi,
        "setup": setup,
        "entry_hint": entry_hint,
        "risk_flags": risk_flags,
        "metrics": metrics,
        "market": row.get("market").cloned().unwrap_or(Value::Null),
        "chain": row.get("chain").cloned().unwrap_or(Value::Null),
        "url": row.get("url").cloned().unwrap_or(Value::Null),
        "scales": {
            "price": PRICE_SCALE,
            "quote": QUOTE_SCALE,
            "score_centi": SCORE_CENTI,
            "change_bps": 100,
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn price_str_and_mul() {
        assert_eq!(price_str(150 * PRICE_SCALE), "150");
        assert_eq!(mul_div(10_000_000, PRICE_SCALE, 150 * PRICE_SCALE), 10_000_000 / 150);
    }
}
