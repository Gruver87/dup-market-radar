"""HTML escape for Telegram + alert formatting."""

from radar.html_escape import escape_html
from radar.telegram.alerts import format_alert


def test_escape_html_basic():
    assert escape_html("<script>") == "&lt;script&gt;"
    assert escape_html("a&b") == "a&amp;b"
    assert escape_html(None) == ""


def test_escape_html_soft_caps_huge_input():
    from radar.html_escape import _MAX_ESCAPE_CHARS

    out = escape_html("Z" * (_MAX_ESCAPE_CHARS + 5000))
    assert len(out) <= _MAX_ESCAPE_CHARS + 1  # ellipsis
    assert out.endswith("…")


def test_format_alert_escapes_symbol_and_reasons():
    text = format_alert(
        {
            "severity": "warn",
            "symbol": "AAA<script>USDT",
            "market": "spot",
            "setup": "fade-watch",
            "price_display": "1.0",
            "score_display": "50.00",
            "action_suggest": "sell",
            "reasons": ["drop <b>fake</b>", "x & y"],
        }
    )
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "&lt;b&gt;fake&lt;/b&gt;" in text
    assert "x &amp; y" in text
    assert "<b>Radar alert" in text  # our markup preserved
