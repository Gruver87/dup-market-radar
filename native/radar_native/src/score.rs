use serde_json::{json, Map, Value};

use crate::classify::{is_meme_symbol, is_stable_symbol, is_usdt_pair, is_non_crypto_futures_symbol};
use crate::rank::{
    as_i64, blend_volume_score_i, candidate, cfg_funding_i, cfg_pct_bps, cfg_price_i,
    cfg_usdt_micros, clamp_i, log_volume_score_i, mul_div, price_str, rank_top, spread_bps_i,
    QUOTE_SCALE, SCORE_CENTI,
};

fn str_list(cfg: &Value, key: &str) -> Vec<String> {
    cfg.get(key)
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default()
}

fn sector_cfg<'a>(cfg: &'a Value, name: &str) -> &'a Value {
    static EMPTY: Value = Value::Null;
    cfg.get("sectors")
        .and_then(|s| s.get(name))
        .unwrap_or(&EMPTY)
}

pub fn score_all(universe: &Value, cfg: &Value, top_n: usize) -> Value {
    let spot = score_spot_alts(universe, cfg);
    let futures = score_futures(universe, cfg);
    let memes = score_memes(universe, cfg);
    let stables = score_stables(universe, cfg);
    let listings = score_listings(universe, cfg);
    let whales = score_whales(universe, cfg);

    let fut_top = rank_top(futures, top_n);
    let futures_long: Vec<Value> = fut_top
        .iter()
        .filter(|c| c.get("setup").and_then(|s| s.as_str()) == Some("long"))
        .cloned()
        .take(top_n)
        .collect();
    let futures_short: Vec<Value> = fut_top
        .iter()
        .filter(|c| c.get("setup").and_then(|s| s.as_str()) == Some("short"))
        .cloned()
        .take(top_n)
        .collect();

    json!({
        "backend": "radar_native",
        "money": "integer_fixed_point",
        "sectors": {
            "spot_alts": rank_top(spot, top_n),
            "futures": fut_top,
            "memes": rank_top(memes, top_n),
            "stables": rank_top(stables, top_n),
            "listings": rank_top(listings, top_n),
            "whales": rank_top(whales, top_n),
            "futures_long": futures_long,
            "futures_short": futures_short,
        }
    })
}

fn score_spot_alts(universe: &Value, cfg: &Value) -> Vec<Value> {
    let sec = sector_cfg(cfg, "spot_alts");
    let min_vol_i = cfg_usdt_micros(sec.get("min_quote_volume_usdt"), 500_000);
    let min_price_i = cfg_price_i(sec.get("min_price"), 100);
    let stables = str_list(cfg, "stable_bases");
    let memes = str_list(cfg, "meme_keywords");
    let mut out = Vec::new();
    let Some(rows) = universe.get("spot").and_then(|v| v.as_array()) else {
        return out;
    };
    for row in rows {
        let Some(sym) = row.get("symbol").and_then(|s| s.as_str()) else {
            continue;
        };
        if !is_usdt_pair(sym) || is_stable_symbol(sym, &stables) || is_meme_symbol(sym, &memes) {
            continue;
        }
        let Some(price) = as_i64(row.get("price_i")) else {
            continue;
        };
        let Some(vol) = as_i64(row.get("quote_volume_i")) else {
            continue;
        };
        let Some(chg_bps) = as_i64(row.get("change_bps")) else {
            continue;
        };
        if price < min_price_i || vol < min_vol_i / 4 {
            continue;
        }
        let chg_1h = as_i64(row.get("change_bps_1h"));
        let chg_15m = as_i64(row.get("change_bps_15m"));
        let chg_4h = as_i64(row.get("change_bps_4h"));
        let mut risks = Vec::new();
        let mom_24 = (chg_bps.abs() * 5) / 2;
        if let Some(c1) = chg_1h {
            if (chg_bps >= 0) != (c1 >= 0) {
                risks.push("mtf_diverge_1h".into());
            }
        }
        if let Some(c15) = chg_15m {
            if (chg_bps >= 0) != (c15 >= 0) {
                risks.push("mtf_diverge_15m".into());
            }
        }
        if let Some(c4) = chg_4h {
            if (chg_bps >= 0) != (c4 >= 0) {
                risks.push("mtf_diverge_4h".into());
            }
        }
        let mom_1h = chg_1h.map(|c| (c.abs() * 5) / 2);
        let mom_15 = chg_15m.map(|c| (c.abs() * 5) / 2);
        let mom_4h = chg_4h.map(|c| (c.abs() * 5) / 2);
        let momentum = match (mom_1h, mom_15, mom_4h) {
            (Some(m1), Some(m15), Some(m4)) => clamp_i(
                (mom_24 * 30 + m4 * 20 + m1 * 30 + m15 * 20) / 100,
                0,
                100 * SCORE_CENTI,
            ),
            (Some(m1), Some(m15), None) => clamp_i(
                (mom_24 * 40 + m1 * 35 + m15 * 25) / 100,
                0,
                100 * SCORE_CENTI,
            ),
            (Some(m1), None, Some(m4)) => {
                clamp_i((mom_24 * 45 + m4 * 20 + m1 * 35) / 100, 0, 100 * SCORE_CENTI)
            }
            (None, Some(m15), Some(m4)) => {
                clamp_i((mom_24 * 45 + m4 * 20 + m15 * 35) / 100, 0, 100 * SCORE_CENTI)
            }
            (Some(m1), None, None) => {
                clamp_i((mom_24 * 55 + m1 * 45) / 100, 0, 100 * SCORE_CENTI)
            }
            (None, Some(m15), None) => {
                clamp_i((mom_24 * 60 + m15 * 40) / 100, 0, 100 * SCORE_CENTI)
            }
            (None, None, Some(m4)) => {
                clamp_i((mom_24 * 70 + m4 * 30) / 100, 0, 100 * SCORE_CENTI)
            }
            (None, None, None) => clamp_i(mom_24, 0, 100 * SCORE_CENTI),
        };
        let vol_vs = as_i64(row.get("vol_vs_median_bps"));
        if let Some(vs) = vol_vs {
            if vs >= 5000 {
                risks.push("vol_surge_1h".into());
            } else if vs <= -5000 {
                risks.push("vol_dry_1h".into());
            }
        }
        let vol_s = blend_volume_score_i(Some(vol), min_vol_i, vol_vs);
        let spr = spread_bps_i(row);
        let mut spread_pen = 0i64;
        if let Some(spr) = spr {
            if spr > 25 {
                spread_pen = (25 * SCORE_CENTI).min((spr * SCORE_CENTI) / 2);
                risks.push(format!("wide_spread_{spr}bps"));
            }
        }
        if vol < min_vol_i {
            risks.push("below_vol_floor".into());
        }
        let range_bps = match (as_i64(row.get("high_i")), as_i64(row.get("low_i"))) {
            (Some(hi), Some(lo)) if price > 0 && hi >= lo => {
                let rb = mul_div(hi - lo, 10_000, price);
                if rb > 800 {
                    risks.push("wide_range_24h".into());
                }
                Some(rb)
            }
            _ => None,
        };
        let third = chg_bps.abs().min(2000) / 10;
        let score = clamp_i(
            mul_div(45, momentum, 100) + mul_div(45, vol_s, 100) + third - spread_pen,
            0,
            100 * SCORE_CENTI,
        );
        let bias = match (chg_15m, chg_1h) {
            (Some(c15), _) if c15.abs() >= 30 => c15,
            (_, Some(c1)) if c1.abs() >= 50 => c1,
            _ => chg_bps,
        };
        let (setup, entry) = if bias >= 0 {
            let lo = mul_div(price, 985, 1000);
            (
                "buy-watch",
                format!(
                    "watch pullback near {}–{}",
                    price_str(lo),
                    price_str(price)
                ),
            )
        } else {
            let hi = mul_div(price, 1015, 1000);
            (
                "fade-watch",
                format!(
                    "watch bounce zone {}–{}",
                    price_str(price),
                    price_str(hi)
                ),
            )
        };
        let mut metrics = Map::new();
        metrics.insert("change_bps".into(), json!(chg_bps));
        metrics.insert("quote_volume_i".into(), json!(vol));
        metrics.insert(
            "spread_bps".into(),
            spr.map(Value::from).unwrap_or(Value::Null),
        );
        if let Some(c1) = chg_1h {
            metrics.insert("change_bps_1h".into(), json!(c1));
        }
        if let Some(c15) = chg_15m {
            metrics.insert("change_bps_15m".into(), json!(c15));
        }
        if let Some(c4) = chg_4h {
            metrics.insert("change_bps_4h".into(), json!(c4));
        }
        if let Some(vs) = vol_vs {
            metrics.insert("vol_vs_median_bps".into(), json!(vs));
        }
        if let Some(rb) = range_bps {
            metrics.insert("range_bps_24h".into(), json!(rb));
        }
        out.push(candidate(
            "spot_alts", row, score, setup, &entry, risks, metrics,
        ));
    }
    out
}

fn blend_mtf_momentum(
    chg_bps: i64,
    chg_1h: Option<i64>,
    chg_15m: Option<i64>,
    chg_4h: Option<i64>,
) -> (i64, Vec<String>) {
    let mut risks = Vec::new();
    let mom_24 = (chg_bps.abs() * 5) / 2;
    if let Some(c1) = chg_1h {
        if (chg_bps >= 0) != (c1 >= 0) {
            risks.push("mtf_diverge_1h".into());
        }
    }
    if let Some(c15) = chg_15m {
        if (chg_bps >= 0) != (c15 >= 0) {
            risks.push("mtf_diverge_15m".into());
        }
    }
    if let Some(c4) = chg_4h {
        if (chg_bps >= 0) != (c4 >= 0) {
            risks.push("mtf_diverge_4h".into());
        }
    }
    let mom_1h = chg_1h.map(|c| (c.abs() * 5) / 2);
    let mom_15 = chg_15m.map(|c| (c.abs() * 5) / 2);
    let mom_4h = chg_4h.map(|c| (c.abs() * 5) / 2);
    let momentum = match (mom_1h, mom_15, mom_4h) {
        (Some(m1), Some(m15), Some(m4)) => {
            clamp_i((mom_24 * 30 + m4 * 20 + m1 * 30 + m15 * 20) / 100, 0, 100 * SCORE_CENTI)
        }
        (Some(m1), Some(m15), None) => {
            clamp_i((mom_24 * 40 + m1 * 35 + m15 * 25) / 100, 0, 100 * SCORE_CENTI)
        }
        (Some(m1), None, Some(m4)) => {
            clamp_i((mom_24 * 45 + m4 * 20 + m1 * 35) / 100, 0, 100 * SCORE_CENTI)
        }
        (None, Some(m15), Some(m4)) => {
            clamp_i((mom_24 * 45 + m4 * 20 + m15 * 35) / 100, 0, 100 * SCORE_CENTI)
        }
        (Some(m1), None, None) => clamp_i((mom_24 * 55 + m1 * 45) / 100, 0, 100 * SCORE_CENTI),
        (None, Some(m15), None) => {
            clamp_i((mom_24 * 60 + m15 * 40) / 100, 0, 100 * SCORE_CENTI)
        }
        (None, None, Some(m4)) => clamp_i((mom_24 * 70 + m4 * 30) / 100, 0, 100 * SCORE_CENTI),
        (None, None, None) => clamp_i(mom_24, 0, 100 * SCORE_CENTI),
    };
    (momentum, risks)
}

fn mtf_bias_bps(chg_bps: i64, chg_1h: Option<i64>, chg_15m: Option<i64>) -> i64 {
    match (chg_15m, chg_1h) {
        (Some(c15), _) if c15.abs() >= 30 => c15,
        (_, Some(c1)) if c1.abs() >= 50 => c1,
        _ => chg_bps,
    }
}

fn score_futures(universe: &Value, cfg: &Value) -> Vec<Value> {
    let sec = sector_cfg(cfg, "futures");
    let min_oi_i = cfg_usdt_micros(sec.get("min_open_interest_usdt"), 1_000_000);
    let extreme_fr_i = as_i64(sec.get("extreme_funding_rate_i"))
        .unwrap_or_else(|| cfg_funding_i(sec.get("extreme_funding_abs"), 50_000));
    let mut out = Vec::new();
    let Some(rows) = universe.get("futures").and_then(|v| v.as_array()) else {
        return out;
    };
    for row in rows {
        let Some(sym) = row.get("symbol").and_then(|s| s.as_str()) else {
            continue;
        };
        if !is_usdt_pair(sym) {
            continue;
        }
        let extra: Vec<String> = sec
            .get("exclude_bases")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|x| x.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();
        if is_non_crypto_futures_symbol(sym, &extra) {
            continue;
        }
        let Some(price) = as_i64(row.get("price_i")) else {
            continue;
        };
        let Some(chg_bps) = as_i64(row.get("change_bps")) else {
            continue;
        };
        let vol = as_i64(row.get("quote_volume_i"));
        let fr_i = as_i64(row.get("funding_rate_i"));
        let oi = as_i64(row.get("open_interest_i"));
        let chg_1h = as_i64(row.get("change_bps_1h"));
        let chg_15m = as_i64(row.get("change_bps_15m"));
        let chg_4h = as_i64(row.get("change_bps_4h"));

        let (mom, mut risks) = blend_mtf_momentum(chg_bps, chg_1h, chg_15m, chg_4h);
        let oi_s = if let Some(oi_v) = oi {
            if oi_v < min_oi_i {
                risks.push("low_oi".into());
            }
            log_volume_score_i(Some(oi_v), min_oi_i)
        } else {
            risks.push("oi_missing".into());
            0
        };
        let vol_s = log_volume_score_i(vol, 1_000_000 * QUOTE_SCALE);
        let bias = mtf_bias_bps(chg_bps, chg_1h, chg_15m);
        let mut side = if bias >= 0 { "long" } else { "short" };
        if let Some(fr) = fr_i {
            if fr >= extreme_fr_i {
                risks.push("extreme_positive_funding".into());
                if bias > 0 {
                    side = "short";
                }
            } else if fr <= -extreme_fr_i {
                risks.push("extreme_negative_funding".into());
                if bias < 0 {
                    side = "long";
                }
            }
        }
        let mut confluence = 15 * SCORE_CENTI;
        if oi.map(|v| v >= min_oi_i).unwrap_or(false) {
            confluence += 20 * SCORE_CENTI;
        }
        let score = clamp_i(
            mul_div(35, mom, 100)
                + mul_div(30, oi_s, 100)
                + mul_div(25, vol_s, 100)
                + mul_div(10, confluence, 100),
            0,
            100 * SCORE_CENTI,
        );
        let entry = match fr_i {
            Some(fr) => format!(
                "{side} watch; mark≈{}; funding_i={fr}",
                price_str(price)
            ),
            None => format!("{side} watch; last≈{}", price_str(price)),
        };
        let mut metrics = Map::new();
        metrics.insert("change_bps".into(), json!(chg_bps));
        metrics.insert(
            "funding_rate_i".into(),
            fr_i.map(Value::from).unwrap_or(Value::Null),
        );
        metrics.insert(
            "open_interest_i".into(),
            oi.map(Value::from).unwrap_or(Value::Null),
        );
        metrics.insert(
            "quote_volume_i".into(),
            vol.map(Value::from).unwrap_or(Value::Null),
        );
        if let Some(c1) = chg_1h {
            metrics.insert("change_bps_1h".into(), json!(c1));
        }
        if let Some(c15) = chg_15m {
            metrics.insert("change_bps_15m".into(), json!(c15));
        }
        if let Some(c4) = chg_4h {
            metrics.insert("change_bps_4h".into(), json!(c4));
        }
        out.push(candidate(
            "futures", row, score, side, &entry, risks, metrics,
        ));
    }
    out
}

fn blend_dex_momentum(
    chg_bps: i64,
    chg_1h: Option<i64>,
    chg_5m: Option<i64>,
) -> (i64, Vec<String>) {
    let mut risks = Vec::new();
    let mom_24 = (chg_bps.abs() * 5) / 2;
    if let Some(c1) = chg_1h {
        if (chg_bps >= 0) != (c1 >= 0) {
            risks.push("mtf_diverge_1h".into());
        }
    }
    if let Some(c5) = chg_5m {
        if (chg_bps >= 0) != (c5 >= 0) {
            risks.push("mtf_diverge_5m".into());
        }
    }
    let mom_1h = chg_1h.map(|c| (c.abs() * 5) / 2);
    let mom_5m = chg_5m.map(|c| (c.abs() * 5) / 2);
    let momentum = match (mom_1h, mom_5m) {
        (Some(m1), Some(m5)) => {
            clamp_i((mom_24 * 40 + m1 * 35 + m5 * 25) / 100, 0, 100 * SCORE_CENTI)
        }
        (Some(m1), None) => clamp_i((mom_24 * 55 + m1 * 45) / 100, 0, 100 * SCORE_CENTI),
        (None, Some(m5)) => clamp_i((mom_24 * 60 + m5 * 40) / 100, 0, 100 * SCORE_CENTI),
        (None, None) => clamp_i(mom_24, 0, 100 * SCORE_CENTI),
    };
    (momentum, risks)
}

fn dex_bias_bps(chg_bps: i64, chg_1h: Option<i64>, chg_5m: Option<i64>) -> i64 {
    match (chg_5m, chg_1h) {
        (Some(c5), _) if c5.abs() >= 25 => c5,
        (_, Some(c1)) if c1.abs() >= 50 => c1,
        _ => chg_bps,
    }
}

fn score_memes(universe: &Value, cfg: &Value) -> Vec<Value> {
    let sec = sector_cfg(cfg, "memes");
    let min_liq_i = cfg_usdt_micros(sec.get("min_liquidity_usd"), 50_000);
    let max_age_h = as_i64(sec.get("max_age_hours")).unwrap_or(168);
    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0);
    let mut out = Vec::new();
    let Some(rows) = universe.get("memes").and_then(|v| v.as_array()) else {
        return out;
    };
    for row in rows {
        let Some(liq) = as_i64(row.get("liquidity_i")) else {
            continue;
        };
        if liq < min_liq_i / 2 {
            continue;
        }
        let mut risks = Vec::new();
        if liq < min_liq_i {
            risks.push("thin_liquidity".into());
        }
        let vol = as_i64(row.get("quote_volume_i"));
        let chg_1h = as_i64(row.get("change_bps_1h"));
        let chg_5m = as_i64(row.get("change_bps_5m"));
        let mut chg_24 = as_i64(row.get("change_bps"));
        if chg_24.is_none() {
            chg_24 = chg_1h;
        }
        let Some(chg_bps) = chg_24 else {
            continue;
        };
        let created = row
            .get("pair_created_at_ms")
            .and_then(|v| v.as_i64().or_else(|| v.as_u64().map(|u| u as i64)));
        let age_h = created.map(|c| ((now_ms - c) / 3_600_000).max(0));
        if let Some(age) = age_h {
            if age > max_age_h {
                risks.push("aged_pair".into());
            }
        }
        let liq_s = log_volume_score_i(Some(liq), min_liq_i);
        let vol_s = log_volume_score_i(vol, min_liq_i);
        let (mom, mtf_risks) = blend_dex_momentum(chg_bps, chg_1h, chg_5m);
        risks.extend(mtf_risks);
        let age_bonus = match age_h {
            Some(a) if a < 24 => 25 * SCORE_CENTI,
            Some(a) if a < 72 => 18 * SCORE_CENTI,
            _ => 10 * SCORE_CENTI,
        };
        let score = clamp_i(
            mul_div(30, liq_s, 100)
                + mul_div(30, vol_s, 100)
                + mul_div(25, mom, 100)
                + mul_div(15, age_bonus, 100),
            0,
            100 * SCORE_CENTI,
        );
        let bias = dex_bias_bps(chg_bps, chg_1h, chg_5m);
        let setup = if bias >= 0 { "buy-watch" } else { "fade-watch" };
        let entry = match age_h {
            Some(a) => format!("DEX watch; liq_i={liq}; age_h={a}"),
            None => format!("DEX watch; liq_i={liq}"),
        };
        let mut metrics = Map::new();
        metrics.insert("liquidity_i".into(), json!(liq));
        metrics.insert(
            "quote_volume_i".into(),
            vol.map(Value::from).unwrap_or(Value::Null),
        );
        metrics.insert("change_bps".into(), json!(chg_bps));
        if let Some(c1) = chg_1h {
            metrics.insert("change_bps_1h".into(), json!(c1));
        }
        if let Some(c5) = chg_5m {
            metrics.insert("change_bps_5m".into(), json!(c5));
        }
        metrics.insert(
            "age_hours".into(),
            age_h.map(Value::from).unwrap_or(Value::Null),
        );
        metrics.insert(
            "chain".into(),
            row.get("chain").cloned().unwrap_or(Value::Null),
        );
        out.push(candidate(
            "memes", row, score, setup, &entry, risks, metrics,
        ));
    }

    // CEX spot memes (KuCoin-first) — auto-tradeable; DEX rows stay watchlist-only.
    let include_cex = sec
        .get("include_cex_spot")
        .and_then(|v| v.as_bool())
        .unwrap_or(true);
    if include_cex {
        let keywords = str_list(cfg, "meme_keywords");
        let stables = str_list(cfg, "stable_bases");
        let min_cex_vol = cfg_usdt_micros(sec.get("min_cex_quote_volume_usdt"), 200_000);
        let mut seen: Vec<String> = out
            .iter()
            .filter_map(|c| c.get("symbol").and_then(|s| s.as_str()).map(|s| s.to_uppercase()))
            .collect();
        let venue_rank = |v: &str| -> i32 {
            match v {
                "kucoin" => 0,
                "binance" => 1,
                "bybit" => 2,
                "mexc" => 3,
                _ => 9,
            }
        };
        let mut best: Map<String, Value> = Map::new();
        if let Some(spot_rows) = universe.get("spot").and_then(|v| v.as_array()) {
            for row in spot_rows {
                let venue = row
                    .get("venue")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_lowercase();
                if !matches!(venue.as_str(), "kucoin" | "binance" | "bybit" | "mexc") {
                    continue;
                }
                let Some(sym) = row.get("symbol").and_then(|s| s.as_str()) else {
                    continue;
                };
                if !is_usdt_pair(sym) || is_stable_symbol(sym, &stables) {
                    continue;
                }
                if !is_meme_symbol(sym, &keywords) {
                    continue;
                }
                let sym_u = sym.to_uppercase();
                if seen.iter().any(|s| s == &sym_u) {
                    continue;
                }
                let Some(price) = as_i64(row.get("price_i")) else {
                    continue;
                };
                let Some(vol) = as_i64(row.get("quote_volume_i")) else {
                    continue;
                };
                let Some(chg_bps) = as_i64(row.get("change_bps")) else {
                    continue;
                };
                let _ = (price, chg_bps);
                if vol < min_cex_vol / 4 {
                    continue;
                }
                if let Some(prev) = best.get(&sym_u) {
                    let prev_v = prev
                        .get("venue")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_lowercase();
                    if venue_rank(&venue) >= venue_rank(&prev_v) {
                        continue;
                    }
                }
                best.insert(sym_u, row.clone());
            }
        }
        for (_sym_u, row) in best {
            let Some(vol) = as_i64(row.get("quote_volume_i")) else {
                continue;
            };
            let Some(chg_bps) = as_i64(row.get("change_bps")) else {
                continue;
            };
            let chg_1h = as_i64(row.get("change_bps_1h"));
            let chg_15m = as_i64(row.get("change_bps_15m"));
            let chg_4h = as_i64(row.get("change_bps_4h"));
            let mut risks = Vec::new();
            let (mom, mtf_risks) = blend_mtf_momentum(chg_bps, chg_1h, chg_15m, chg_4h);
            risks.extend(mtf_risks);
            let vol_vs = as_i64(row.get("vol_vs_median_bps"));
            let vol_s = blend_volume_score_i(Some(vol), min_cex_vol, vol_vs);
            if vol < min_cex_vol {
                risks.push("below_vol_floor".into());
            }
            let score = clamp_i(
                mul_div(50, mom, 100) + mul_div(50, vol_s, 100),
                0,
                100 * SCORE_CENTI,
            );
            let bias = mtf_bias_bps(chg_bps, chg_1h, chg_15m);
            let setup = if bias >= 0 { "buy-watch" } else { "fade-watch" };
            let venue = row
                .get("venue")
                .and_then(|v| v.as_str())
                .unwrap_or("cex");
            let entry = format!("CEX meme ({venue}); spot MTF watch");
            let mut metrics = Map::new();
            metrics.insert("change_bps".into(), json!(chg_bps));
            metrics.insert("quote_volume_i".into(), json!(vol));
            metrics.insert("source".into(), json!("cex"));
            metrics.insert("venue".into(), json!(venue));
            if let Some(c) = chg_1h {
                metrics.insert("change_bps_1h".into(), json!(c));
            }
            if let Some(c) = chg_15m {
                metrics.insert("change_bps_15m".into(), json!(c));
            }
            if let Some(c) = chg_4h {
                metrics.insert("change_bps_4h".into(), json!(c));
            }
            if let Some(v) = vol_vs {
                metrics.insert("vol_vs_median_bps".into(), json!(v));
            }
            out.push(candidate(
                "memes", &row, score, setup, &entry, risks, metrics,
            ));
            if let Some(sym) = row.get("symbol").and_then(|s| s.as_str()) {
                seen.push(sym.to_uppercase());
            }
        }
    }
    out
}

fn score_stables(universe: &Value, cfg: &Value) -> Vec<Value> {
    let sec = sector_cfg(cfg, "stables");
    let warn_bps = as_i64(sec.get("depeg_warn_bps"))
        .unwrap_or_else(|| cfg_pct_bps(sec.get("depeg_warn_pct"), 30));
    let mut out = Vec::new();
    let Some(rows) = universe.get("stables").and_then(|v| v.as_array()) else {
        return out;
    };
    for row in rows {
        let Some(price) = as_i64(row.get("price_i")) else {
            continue;
        };
        let Some(peg_bps) = as_i64(row.get("peg_deviation_bps")) else {
            continue;
        };
        let mut risks = Vec::new();
        if peg_bps >= warn_bps {
            risks.push("depeg_warn".into());
        }
        let vol = as_i64(row.get("quote_volume_i")).unwrap_or(0);
        let peg_term = mul_div(peg_bps * 40, SCORE_CENTI, 100);
        let vol_usdt_units = vol / QUOTE_SCALE.max(1);
        let vol_term = (vol_usdt_units / 1_000_000_000).min(20) * SCORE_CENTI;
        let score = clamp_i(peg_term + vol_term, 0, 100 * SCORE_CENTI);
        let entry = format!(
            "peg {} (|dev|={peg_bps}bps) — watch only",
            price_str(price)
        );
        let mut metrics = Map::new();
        metrics.insert("price_i".into(), json!(price));
        metrics.insert("peg_deviation_bps".into(), json!(peg_bps));
        metrics.insert("quote_volume_i".into(), json!(vol));
        out.push(candidate(
            "stables",
            row,
            score,
            "peg-monitor",
            &entry,
            risks,
            metrics,
        ));
    }
    out
}

fn index_live_rows<'a>(
    rows: Option<&'a Vec<Value>>,
) -> (
    std::collections::HashMap<(String, String), &'a Value>,
    std::collections::HashMap<String, &'a Value>,
) {
    let mut by_vs = std::collections::HashMap::new();
    let mut by_sym = std::collections::HashMap::new();
    let Some(rows) = rows else {
        return (by_vs, by_sym);
    };
    for r in rows {
        let venue = r
            .get("venue")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let symbol = r
            .get("symbol")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if symbol.is_empty() {
            continue;
        }
        by_vs.insert((venue, symbol.clone()), r);
        let vol = as_i64(r.get("quote_volume_i")).unwrap_or(0);
        let replace = match by_sym.get(&symbol) {
            None => true,
            Some(prev) => vol >= as_i64(prev.get("quote_volume_i")).unwrap_or(0),
        };
        if replace {
            by_sym.insert(symbol, r);
        }
    }
    (by_vs, by_sym)
}

fn resolve_listing_live<'a>(
    row: &Value,
    spot_vs: &std::collections::HashMap<(String, String), &'a Value>,
    spot_sym: &std::collections::HashMap<String, &'a Value>,
    fut_vs: &std::collections::HashMap<(String, String), &'a Value>,
    fut_sym: &std::collections::HashMap<String, &'a Value>,
) -> Option<&'a Value> {
    let venue = row.get("venue").and_then(|v| v.as_str()).unwrap_or("");
    let symbol = row.get("symbol").and_then(|v| v.as_str()).unwrap_or("");
    let market = row
        .get("market")
        .and_then(|v| v.as_str())
        .unwrap_or("spot")
        .to_ascii_lowercase();
    let key = (venue.to_string(), symbol.to_string());
    if market == "futures" {
        return fut_vs
            .get(&key)
            .copied()
            .or_else(|| fut_sym.get(symbol).copied());
    }
    spot_vs
        .get(&key)
        .copied()
        .or_else(|| spot_sym.get(symbol).copied())
        .or_else(|| fut_vs.get(&key).copied())
        .or_else(|| fut_sym.get(symbol).copied())
}

fn score_listings(universe: &Value, cfg: &Value) -> Vec<Value> {
    let sec = sector_cfg(cfg, "listings");
    let lookback_h = as_i64(sec.get("lookback_hours")).unwrap_or(48);
    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0);
    let cutoff = now_ms - lookback_h * 3_600_000;

    let (spot_vs, spot_sym) =
        index_live_rows(universe.get("spot").and_then(|v| v.as_array()));
    let (fut_vs, fut_sym) =
        index_live_rows(universe.get("futures").and_then(|v| v.as_array()));

    let mut out = Vec::new();
    let Some(rows) = universe.get("listings").and_then(|v| v.as_array()) else {
        return out;
    };
    for row in rows {
        let Some(ts) = row
            .get("listed_at_ms")
            .and_then(|v| v.as_i64().or_else(|| v.as_u64().map(|u| u as i64)))
        else {
            continue;
        };
        if ts < cutoff {
            continue;
        }
        let age_h = ((now_ms - ts) / 3_600_000).max(0);
        let live = resolve_listing_live(row, &spot_vs, &spot_sym, &fut_vs, &fut_sym);
        let vol = live.and_then(|r| as_i64(r.get("quote_volume_i")));
        let chg_bps = live.and_then(|r| as_i64(r.get("change_bps")));
        let chg_1h = live.and_then(|r| as_i64(r.get("change_bps_1h")));
        let chg_15m = live.and_then(|r| as_i64(r.get("change_bps_15m")));
        let chg_4h = live.and_then(|r| as_i64(r.get("change_bps_4h")));
        let price = live
            .and_then(|r| as_i64(r.get("price_i")))
            .or_else(|| as_i64(row.get("price_i")));
        // Ghost listings (no live book) — skip.
        if price.is_none() && vol.is_none() {
            continue;
        }
        let mut risks = Vec::new();
        if vol.is_none() {
            risks.push("no_live_volume".into());
        }
        let live_venue = live.and_then(|r| r.get("venue").and_then(|v| v.as_str()));
        let list_venue = row.get("venue").and_then(|v| v.as_str());
        if let (Some(lv), Some(rv)) = (live_venue, list_venue) {
            if lv != rv {
                risks.push("cross_venue_live".into());
            }
        }
        let freshness = clamp_i(
            100 * SCORE_CENTI
                - mul_div(age_h * 100 * SCORE_CENTI, 1, lookback_h.max(1)),
            0,
            100 * SCORE_CENTI,
        );
        let vol_s = if let Some(v) = vol {
            clamp_i(
                mul_div(v, 40 * SCORE_CENTI, 5_000_000 * QUOTE_SCALE),
                0,
                100 * SCORE_CENTI,
            )
        } else {
            0
        };
        let mom = if let Some(c) = chg_bps {
            let (m, mtf_risks) = blend_mtf_momentum(c, chg_1h, chg_15m, chg_4h);
            risks.extend(mtf_risks);
            m
        } else {
            0
        };
        let score = clamp_i(
            mul_div(45, freshness, 100) + mul_div(35, vol_s, 100) + mul_div(20, mom, 100),
            0,
            100 * SCORE_CENTI,
        );
        let mut merged = row.as_object().cloned().unwrap_or_default();
        if let Some(p) = price {
            merged.insert("price_i".into(), json!(p));
        }
        if let Some(c) = chg_bps {
            merged.insert("change_bps".into(), json!(c));
        }
        if let Some(v) = vol {
            merged.insert("quote_volume_i".into(), json!(v));
        }
        merged
            .entry("market".to_string())
            .or_insert_with(|| json!("spot"));
        let merged_v = Value::Object(merged);
        let entry = format!("listed ~{age_h}h ago; vol_i={vol:?}");
        let mut metrics = Map::new();
        metrics.insert("age_hours".into(), json!(age_h));
        metrics.insert("listed_at_ms".into(), json!(ts));
        metrics.insert(
            "quote_volume_i".into(),
            vol.map(Value::from).unwrap_or(Value::Null),
        );
        metrics.insert(
            "change_bps".into(),
            chg_bps.map(Value::from).unwrap_or(Value::Null),
        );
        if let Some(lv) = live_venue {
            metrics.insert("live_venue".into(), json!(lv));
        }
        if let Some(c1) = chg_1h {
            metrics.insert("change_bps_1h".into(), json!(c1));
        }
        if let Some(c15) = chg_15m {
            metrics.insert("change_bps_15m".into(), json!(c15));
        }
        if let Some(c4) = chg_4h {
            metrics.insert("change_bps_4h".into(), json!(c4));
        }
        out.push(candidate(
            "listings",
            &merged_v,
            score,
            "new-listing-watch",
            &entry,
            risks,
            metrics,
        ));
    }
    out
}

fn score_whales(universe: &Value, cfg: &Value) -> Vec<Value> {
    let sec = sector_cfg(cfg, "whales");
    // Aggregate-first: default 10k USDT (KuCoin histories rarely have single ≥100k prints).
    let min_n = cfg_usdt_micros(sec.get("min_notional_usdt"), 10_000);
    let dust = match sec.get("min_print_usdt") {
        Some(v) => cfg_usdt_micros(Some(v), 100),
        None => (min_n / 50).max(100 * 1_000_000),
    };
    let mut spot_by_sym: std::collections::HashMap<String, &Value> =
        std::collections::HashMap::new();
    if let Some(rows) = universe.get("spot").and_then(|v| v.as_array()) {
        for r in rows {
            if let Some(sym) = r.get("symbol").and_then(|s| s.as_str()) {
                spot_by_sym.insert(sym.to_string(), r);
            }
        }
    }
    let mut buckets: std::collections::HashMap<(String, String, String), Map<String, Value>> =
        std::collections::HashMap::new();
    let Some(rows) = universe.get("whales").and_then(|v| v.as_array()) else {
        return Vec::new();
    };
    for row in rows {
        let notional = as_i64(row.get("notional_i")).unwrap_or(0);
        if notional < dust {
            continue;
        }
        let venue = row
            .get("venue")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let symbol = row
            .get("symbol")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let side = row
            .get("side")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string();
        let key = (venue.clone(), symbol.clone(), side.clone());
        let b = buckets.entry(key).or_insert_with(|| {
            let mut m = Map::new();
            m.insert("venue".into(), json!(venue));
            m.insert("symbol".into(), json!(symbol));
            m.insert("side".into(), json!(side));
            m.insert("notional_i".into(), json!(0i64));
            m.insert("prints".into(), json!(0));
            m.insert(
                "price_i".into(),
                row.get("price_i").cloned().unwrap_or(Value::Null),
            );
            m.insert("market".into(), json!("spot"));
            m
        });
        let prev = as_i64(b.get("notional_i")).unwrap_or(0);
        b.insert("notional_i".into(), json!(prev + notional));
        let prints = as_i64(b.get("prints")).unwrap_or(0);
        b.insert("prints".into(), json!(prints + 1));
        if let Some(p) = row.get("price_i") {
            b.insert("price_i".into(), p.clone());
        }
    }
    let mut out = Vec::new();
    for b in buckets.into_values() {
        let notional = as_i64(b.get("notional_i")).unwrap_or(0);
        if notional < min_n {
            continue;
        }
        let prints = as_i64(b.get("prints")).unwrap_or(0);
        let side = b
            .get("side")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_lowercase();
        let ratio = (notional / min_n.max(1)).min(20);
        let mut score = clamp_i(ratio * 5 * SCORE_CENTI, 0, 100 * SCORE_CENTI);
        let setup = format!("whale-{side}");
        let entry = format!(
            "size-based print sum micros={notional} ({prints} trades) — not labeled smart money"
        );
        let row = Value::Object(b.clone());
        let mut risks = vec!["size_proxy_only".into()];
        let mut metrics = Map::new();
        metrics.insert("notional_i".into(), json!(notional));
        metrics.insert("prints".into(), json!(prints));
        metrics.insert("side".into(), json!(side));
        if let Some(sym) = b.get("symbol").and_then(|s| s.as_str()) {
            if let Some(spot) = spot_by_sym.get(sym) {
                if let Some(chg) = as_i64(spot.get("change_bps")) {
                    let chg_1h = as_i64(spot.get("change_bps_1h"));
                    let chg_15m = as_i64(spot.get("change_bps_15m"));
                    let bias = mtf_bias_bps(chg, chg_1h, chg_15m);
                    metrics.insert("spot_bias_bps".into(), json!(bias));
                    if let Some(c15) = chg_15m {
                        metrics.insert("change_bps_15m".into(), json!(c15));
                    }
                    if let Some(c1) = chg_1h {
                        metrics.insert("change_bps_1h".into(), json!(c1));
                    }
                    let whale_buy = side == "buy";
                    let whale_sell = side == "sell";
                    if (whale_buy && bias > 0) || (whale_sell && bias < 0) {
                        score = clamp_i(score + 8 * SCORE_CENTI, 0, 100 * SCORE_CENTI);
                        risks.push("spot_mtf_align".into());
                    } else if (whale_buy && bias < 0) || (whale_sell && bias > 0) {
                        risks.push("spot_mtf_diverge".into());
                    }
                }
            }
        }
        out.push(candidate(
            "whales",
            &row,
            score,
            &setup,
            &entry,
            risks,
            metrics,
        ));
    }
    out
}
