"""Quiet Ctrl+C filter for uvicorn ERROR noise."""

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_radar_server import _ShutdownNoiseFilter  # noqa: E402


def test_filter_drops_keyboard_interrupt_message():
    f = _ShutdownNoiseFilter()
    rec = logging.LogRecord(
        name="uvicorn.error",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="Traceback...\nKeyboardInterrupt\nCancelledError",
        args=(),
        exc_info=None,
    )
    assert f.filter(rec) is False


def test_filter_keeps_real_errors():
    f = _ShutdownNoiseFilter()
    rec = logging.LogRecord(
        name="uvicorn.error",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="Application startup failed: bind address already in use",
        args=(),
        exc_info=None,
    )
    assert f.filter(rec) is True


def test_filter_drops_cancelled_exc_info():
    f = _ShutdownNoiseFilter()
    try:
        raise asyncio.CancelledError()
    except asyncio.CancelledError:
        exc_info = sys.exc_info()
    rec = logging.LogRecord(
        name="uvicorn.error",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="boom",
        args=(),
        exc_info=exc_info,
    )
    assert f.filter(rec) is False
