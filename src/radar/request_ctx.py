"""Request-scoped context (ASGI) — request id for audit correlation."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

request_id_var: ContextVar[Optional[str]] = ContextVar("radar_request_id", default=None)


def get_request_id() -> Optional[str]:
    return request_id_var.get()


def set_request_id(rid: Optional[str]) -> None:
    request_id_var.set(rid)
