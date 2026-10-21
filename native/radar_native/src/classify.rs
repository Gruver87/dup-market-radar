pub fn base_asset(symbol: &str) -> String {
    let s = symbol
        .to_uppercase()
        .replace('/', "")
        .replace('-', "")
        .replace('_', "");
    for q in ["USDT", "USDC", "BUSD", "FDUSD", "USD"] {
        if s.ends_with(q) && s.len() > q.len() {
            return s[..s.len() - q.len()].to_string();
        }
    }
    s
}

pub fn is_usdt_pair(symbol: &str) -> bool {
    let s = symbol
        .to_uppercase()
        .replace('/', "")
        .replace('-', "")
        .replace('_', "");
    s.ends_with("USDT")
}

pub fn is_stable_symbol(symbol: &str, stables: &[String]) -> bool {
    let base = base_asset(symbol);
    let up = symbol.to_uppercase();
    stables.iter().any(|x| {
        let xu = x.to_uppercase();
        base == xu || up == xu
    })
}

pub fn is_meme_symbol(symbol: &str, keywords: &[String]) -> bool {
    let base = base_asset(symbol);
    for k in keywords {
        let ku = k.to_uppercase();
        if base == ku {
            return true;
        }
        if ku.len() >= 4 && base.contains(&ku) {
            return true;
        }
    }
    false
}

const DEFAULT_NON_CRYPTO_FUT_BASES: &[&str] = &[
    "XAU", "XAG", "XPD", "XPT", "USOIL", "UKOIL", "WTI", "BRENT", "NATGAS",
    "COPPER", "ALUMINIUM", "ALUMINUM", "NICKEL", "WHEAT", "CORN", "SOYBEAN",
    "COTTON", "SUGAR", "COFFEE", "SPX", "NDX", "DJI", "HSI", "FTSE", "GER40",
    "US30", "NAS100", "EURUSD", "GBPUSD", "USDJPY", "USDCNH",
];

pub fn is_non_crypto_futures_symbol(symbol: &str, extra_bases: &[String]) -> bool {
    let base = base_asset(symbol);
    if base.is_empty() {
        return false;
    }
    if DEFAULT_NON_CRYPTO_FUT_BASES.iter().any(|b| *b == base) {
        return true;
    }
    extra_bases.iter().any(|x| x.to_uppercase() == base)
}

pub fn normalize_kucoin_symbol(sym: &str, futures: bool) -> String {
    let mut s = sym
        .to_uppercase()
        .replace('-', "")
        .replace('_', "");
    if futures && s.ends_with("USDTM") {
        s = s[..s.len() - 1].to_string();
    }
    if s.starts_with("XBT") {
        s = format!("BTC{}", &s[3..]);
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base_and_flags() {
        assert_eq!(base_asset("btc/usdt"), "BTC");
        assert!(is_usdt_pair("ETHUSDT"));
        assert!(is_stable_symbol(
            "USDCUSDT",
            &["USDC".into(), "USDT".into()]
        ));
        assert!(is_meme_symbol("1000PEPEUSDT", &["PEPE".into()]));
        assert_eq!(normalize_kucoin_symbol("XBTUSDTM", true), "BTCUSDT");
        assert!(is_non_crypto_futures_symbol("XAUUSDT", &[]));
        assert!(!is_non_crypto_futures_symbol("BTCUSDT", &[]));
    }
}
