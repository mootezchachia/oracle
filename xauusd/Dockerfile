# ---------------------------------------------------------------------------
# XAUUSD Sentinel
#
# The analysis core has no numeric dependencies, so the image stays small and
# builds in seconds. Only aiohttp and PyYAML are installed.
#
# MetaTrader5 is deliberately NOT installed: it is Windows-only and requires a
# running terminal. In a container the system uses the Yahoo provider, which is
# correct for monitoring, backtesting and alerting. For live execution against
# broker prices, run the monitor on the Windows host beside your terminal.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    TZ=UTC

WORKDIR /app

# Dependencies first so the layer caches across source changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY config ./config
COPY src ./src

# Install the package itself so `python -m xauusd` resolves without PYTHONPATH
# tricks. --no-deps because requirements.txt already pinned them above.
RUN pip install --no-cache-dir --no-deps -e .

# Run as an unprivileged user; the data and log directories must be writable.
RUN useradd --create-home --uid 10001 sentinel \
    && mkdir -p /app/data /app/logs \
    && chown -R sentinel:sentinel /app
USER sentinel

VOLUME ["/app/data", "/app/logs"]
EXPOSE 8787

# The readiness probe reports 503 until enough history has been collected, so
# an orchestrator will not route traffic to a still-warming instance.
HEALTHCHECK --interval=60s --timeout=10s --start-period=180s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=5).status == 200 else 1)"

ENTRYPOINT ["python", "-m", "xauusd"]
CMD ["run"]
