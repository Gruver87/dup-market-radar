#!/usr/bin/env python3
"""ops_http_probe — tiny HTTP GET for ops_board; print body or ERR:<msg>."""

from __future__ import annotations

import sys
import urllib.error
import urllib.request


def main() -> int:
    if len(sys.argv) < 2:
        print("ERR:missing url", file=sys.stderr)
        return 2
    url = sys.argv[1]
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            body = resp.read().decode("utf-8", "replace")
            print(body[:8000])
            return 0
    except Exception as exc:  # noqa: BLE001 — board probe must never crash caller
        print(f"ERR:{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
