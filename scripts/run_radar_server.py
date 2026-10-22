#!/usr/bin/env python3
"""Quiet uvicorn entry for DUP Market Radar (Windows Ctrl+C).

uvicorn re-raises captured SIGINT after a clean shutdown; that KeyboardInterrupt
cancels Starlette lifespan ``receive()`` and uvicorn logs a scary ERROR traceback
even though shutdown already completed. Filter that noise; exit 0 on Ctrl+C.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class _ShutdownNoiseFilter(logging.Filter):
    """Drop uvicorn ERROR records that are only Ctrl+C / lifespan cancel."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.ERROR:
            return True
        msg = record.getMessage()
        if "KeyboardInterrupt" in msg:
            return False
        if "CancelledError" in msg and (
            "lifespan" in msg
            or "receive_queue" in msg
            or "capture_signals" in msg
            or "wrapped_receive" in msg
        ):
            return False
        if record.exc_info and record.exc_info[0] is not None:
            try:
                if issubclass(record.exc_info[0], (KeyboardInterrupt, asyncio.CancelledError)):
                    return False
            except TypeError:
                pass
        return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="DUP Market Radar uvicorn (quiet Ctrl+C)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--reload", action="store_true")
    p.add_argument("--reload-dir", action="append", default=[])
    args = p.parse_args(argv)

    logging.getLogger("uvicorn.error").addFilter(_ShutdownNoiseFilter())
    logging.getLogger("uvicorn").addFilter(_ShutdownNoiseFilter())

    import uvicorn

    kwargs: dict = {
        "app": "radar.api.app:app",
        "host": str(args.host),
        "port": int(args.port),
        "limit_concurrency": 128,
        "timeout_keep_alive": 30,
        "timeout_graceful_shutdown": 8,
    }
    if args.reload:
        kwargs["reload"] = True
        dirs = [str(Path(d)) for d in (args.reload_dir or []) if d]
        if dirs:
            kwargs["reload_dirs"] = dirs

    try:
        uvicorn.run(**kwargs)
    except KeyboardInterrupt:
        print("Stopped.", flush=True)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
