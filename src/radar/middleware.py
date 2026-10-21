"""ASGI middleware — request id, security headers, access log, body cap."""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from typing import Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from radar.request_ctx import request_id_var

logger = logging.getLogger("radar.access")

REQUEST_ID_HEADER = "X-Request-Id"
REQUEST_ID_MAX_LEN = 64
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")
# Default JSON body cap for POST/PUT/PATCH (trade payloads are tiny).
DEFAULT_MAX_BODY_BYTES = 65_536
# Soft refuse absurd request-target (path + query) — DoS / log flood.
DEFAULT_MAX_URL_BYTES = 2_048
# Soft refuse header floods (count + total name+value bytes).
DEFAULT_MAX_HEADER_COUNT = 128
DEFAULT_MAX_HEADER_BYTES = 32_768
_ACCESS_PATH_MAX = 256


def sanitize_request_id(raw: Optional[str]) -> str:
    """Accept client X-Request-Id only if charset/length safe — else mint one."""
    s = (raw or "").strip()
    if _REQUEST_ID_RE.fullmatch(s):
        return s
    return uuid.uuid4().hex[:16]


def sanitize_access_path(path: str) -> str:
    """Strip control chars and truncate for access logs."""
    s = "".join(c for c in str(path or "") if 32 <= ord(c) < 127)
    if not s:
        return "/"
    return s[:_ACCESS_PATH_MAX]

# Ops dashboard CSP — allow self + inline scripts (dashboard is a single HTML file).
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


def _hsts_max_age() -> Optional[int]:
    """Optional HSTS — only when RADAR_HSTS_MAX_AGE is a positive int (HTTPS edge)."""
    raw = (os.environ.get("RADAR_HSTS_MAX_AGE") or "").strip()
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError:
        return None
    if n < 1 or n > 63_072_000:  # ≤2y
        return None
    return n


_NO_STORE_PREFIXES = (
    "/api/trade",
    "/api/telegram",
    "/api/health",
    "/api/ready",
    "/api/metrics",
    "/api/live",
    "/api/ops",
    "/api/snapshot",
    "/api/sector",
)
# Exact paths (dashboard HTML must not be long-cached with embedded ops JS)
_NO_STORE_EXACT = frozenset({"/", "/index.html"})


_REFUSED_METHODS = frozenset({"TRACE", "TRACK"})


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline browser hardening for the ops UI + JSON API."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method.upper() in _REFUSED_METHODS:
            return JSONResponse(
                {"detail": "method refused"}, status_code=405
            )
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        response.headers.setdefault("Content-Security-Policy", _CSP)
        # HTTPS terminators may strip; harmless on localhost http
        response.headers.setdefault(
            "Cross-Origin-Opener-Policy", "same-origin"
        )
        response.headers.setdefault(
            "Cross-Origin-Resource-Policy", "same-origin"
        )
        response.headers.setdefault(
            "X-Permitted-Cross-Domain-Policies", "none"
        )
        hsts = _hsts_max_age()
        if hsts is not None:
            response.headers.setdefault(
                "Strict-Transport-Security", f"max-age={hsts}"
            )
        # Drop banner leakage if ASGI server set it
        if "server" in response.headers:
            del response.headers["server"]
        path = request.url.path
        if path in _NO_STORE_EXACT or any(
            path == p or path.startswith(p + "/") for p in _NO_STORE_PREFIXES
        ):
            response.headers["Cache-Control"] = "no-store"
        return response


class MaxUrlLengthMiddleware(BaseHTTPMiddleware):
    """Refuse oversized request-target (path + query) — soft 414."""

    def __init__(
        self, app, max_bytes: int = DEFAULT_MAX_URL_BYTES
    ) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._max = max(64, int(max_bytes))

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # path + "?" + query (raw scope when available)
        scope = request.scope
        raw_path = scope.get("raw_path") or b""
        qs = scope.get("query_string") or b""
        if isinstance(raw_path, (bytes, bytearray)):
            n = len(raw_path) + (1 + len(qs) if qs else 0)
        else:
            target = str(request.url.path)
            if request.url.query:
                target = f"{target}?{request.url.query}"
            n = len(target.encode("utf-8", errors="replace"))
        if n > self._max:
            logger.warning("url too long bytes=%s max=%s", n, self._max)
            return JSONResponse(
                {"detail": "request URI too long"}, status_code=414
            )
        return await call_next(request)


class MaxHeadersMiddleware(BaseHTTPMiddleware):
    """Refuse header floods — soft 431 (count + total bytes)."""

    def __init__(
        self,
        app,  # type: ignore[no-untyped-def]
        *,
        max_count: int = DEFAULT_MAX_HEADER_COUNT,
        max_bytes: int = DEFAULT_MAX_HEADER_BYTES,
    ) -> None:
        super().__init__(app)
        self._max_count = max(16, int(max_count))
        self._max_bytes = max(1024, int(max_bytes))

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        hdrs = request.scope.get("headers") or []
        if not isinstance(hdrs, (list, tuple)):
            return await call_next(request)
        n = len(hdrs)
        if n > self._max_count:
            logger.warning("too many headers count=%s max=%s", n, self._max_count)
            return JSONResponse(
                {"detail": "request header fields too large"}, status_code=431
            )
        total = 0
        for pair in hdrs:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            k, v = pair[0], pair[1]
            total += len(k) + len(v)
            if total > self._max_bytes:
                logger.warning(
                    "headers too large bytes=%s max=%s", total, self._max_bytes
                )
                return JSONResponse(
                    {"detail": "request header fields too large"},
                    status_code=431,
                )
        return await call_next(request)


# Trade POSTs that carry a JSON body (cycle has none).
_JSON_TRADE_POSTS = frozenset(
    {
        "/api/trade/order",
        "/api/trade/reconcile",
        "/api/trade/auto",
    }
)


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Refuse oversized declared bodies (413) + non-JSON trade POSTs (415)."""

    def __init__(self, app, max_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        super().__init__(app)
        self._max = max(1024, int(max_bytes))

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method in {"POST", "PUT", "PATCH"}:
            path = request.url.path.rstrip("/") or "/"
            if path in _JSON_TRADE_POSTS:
                ct = (
                    (request.headers.get("content-type") or "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if ct != "application/json":
                    return JSONResponse(
                        {"detail": "Content-Type must be application/json"},
                        status_code=415,
                    )
            raw = request.headers.get("content-length")
            # Trade JSON POSTs: require Content-Length — refuse chunked bypass of size gate.
            if path in _JSON_TRADE_POSTS and raw is None:
                return JSONResponse(
                    {"detail": "Content-Length required"},
                    status_code=411,
                )
            if raw is not None:
                try:
                    size = int(raw)
                except ValueError:
                    return JSONResponse(
                        {"detail": "invalid Content-Length"}, status_code=400
                    )
                if size < 0:
                    return JSONResponse(
                        {"detail": "invalid Content-Length"}, status_code=400
                    )
                if size > self._max:
                    logger.warning(
                        "body too large path=%s content_length=%s max=%s",
                        request.url.path,
                        size,
                        self._max,
                    )
                    return JSONResponse(
                        {"detail": "request body too large"}, status_code=413
                    )
        return await call_next(request)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        rid = sanitize_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = rid
        token = request_id_var.set(rid)
        t0 = time.perf_counter()
        path_safe = sanitize_access_path(request.url.path)
        method_safe = "".join(
            c for c in str(request.method or "") if c.isalpha()
        )[:16] or "?"
        try:
            response = await call_next(request)
        except Exception as exc:
            from radar.secrets_redact import redact_exc

            ms = int((time.perf_counter() - t0) * 1000)
            logger.error(
                "request_fail id=%s method=%s path=%s ms=%s err=%s",
                rid,
                method_safe,
                path_safe,
                ms,
                redact_exc(exc, max_len=256),
            )
            raise
        finally:
            request_id_var.reset(token)
        ms = int((time.perf_counter() - t0) * 1000)
        response.headers[REQUEST_ID_HEADER] = rid
        logger.info(
            "request id=%s method=%s path=%s status=%s ms=%s",
            rid,
            method_safe,
            path_safe,
            response.status_code,
            ms,
        )
        return response
