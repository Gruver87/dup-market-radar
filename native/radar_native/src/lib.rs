mod classify;
mod rank;
mod score;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde_json::Value;

/// Score + rank entire universe JSON → sectors JSON (hot path).
#[pyfunction]
fn score_universe_json(universe_json: &str, cfg_json: &str, top_n: u32) -> PyResult<String> {
    let universe: Value = serde_json::from_str(universe_json)
        .map_err(|e| PyValueError::new_err(format!("universe json: {e}")))?;
    let cfg: Value = serde_json::from_str(cfg_json)
        .map_err(|e| PyValueError::new_err(format!("cfg json: {e}")))?;
    let out = score::score_all(&universe, &cfg, top_n as usize);
    serde_json::to_string(&out).map_err(|e| PyValueError::new_err(format!("encode: {e}")))
}

#[pyfunction]
fn normalize_kucoin_symbol(symbol: &str, futures: bool) -> String {
    classify::normalize_kucoin_symbol(symbol, futures)
}

#[pyfunction]
fn native_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// Engine layer ids present in this wheel / workspace build.
#[pyfunction]
fn native_engine_layers() -> Vec<&'static str> {
    vec!["radar_core", "radar_native", "radar_ingest"]
}

/// Parse a decimal price string into Radar price_i (1e8) via radar_core.
#[pyfunction]
fn price_i_from_decimal(raw: &str) -> PyResult<i64> {
    radar_core::price_i_from_str(raw)
        .ok_or_else(|| PyValueError::new_err("bad decimal price"))
}

#[pymodule]
fn radar_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(score_universe_json, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_kucoin_symbol, m)?)?;
    m.add_function(wrap_pyfunction!(native_version, m)?)?;
    m.add_function(wrap_pyfunction!(native_engine_layers, m)?)?;
    m.add_function(wrap_pyfunction!(price_i_from_decimal, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
