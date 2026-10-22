"""Display missing MTF cap default (kept at 8 after 0.2.80 trial)."""

import inspect
from radar.pipeline import _display_missing_mtf_symbols


def test_display_missing_cap_default_is_8():
    sig = inspect.signature(_display_missing_mtf_symbols)
    assert sig.parameters["cap"].default == 8


def test_display_missing_respects_cap():
    sectors = {
        "spot_alts": [{"symbol": f"S{i}USDT", "metrics": {}} for i in range(10)],
        "futures": [{"symbol": f"F{i}USDT", "metrics": {}} for i in range(10)],
    }
    spot, fut = _display_missing_mtf_symbols(sectors)
    assert len(spot) == 8
    assert len(fut) == 8
