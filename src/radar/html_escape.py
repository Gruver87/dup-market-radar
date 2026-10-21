"""HTML escape for Telegram parse_mode=HTML and similar surfaces."""

from __future__ import annotations

from typing import Any

# Soft refuse absurd markup payloads (Telegram send already caps ~4k).
_MAX_ESCAPE_CHARS = 16_384


def escape_html(value: Any) -> str:
    """Escape &, <, > for Telegram HTML (and safe embedding in markup)."""
    if value is None:
        return ""
    s = str(value)
    if len(s) > _MAX_ESCAPE_CHARS:
        s = s[:_MAX_ESCAPE_CHARS] + "…"
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
