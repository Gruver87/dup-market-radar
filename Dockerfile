# DUP Market Radar — ops image (Python path; native optional via volume/maturin)
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    RADAR_NATIVE_PREFER=0 \
    RADAR_NATIVE_FAIL_CLOSED=0 \
    RADAR_REQUIRE_API_KEY=1

WORKDIR /app

RUN useradd --create-home --uid 10001 radar

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config ./config
COPY dashboard ./dashboard
COPY src ./src
COPY scripts ./scripts
RUN mkdir -p /app/data && chown -R radar:radar /app

USER radar
EXPOSE 8787

# Liveness only — readiness is /api/ready (may 503 until warm scan)
# Readiness (may 503 until warm scan) — start-period gives first scan time
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/ready', timeout=3)"

CMD ["python", "-m", "uvicorn", "radar.api.app:app", "--host", "0.0.0.0", "--port", "8787", "--limit-concurrency", "128", "--timeout-keep-alive", "30"]
