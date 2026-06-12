# ============================================================
# Dockerfile — RSI Scanner (Fly.io)
# ============================================================

# ── Stage 1: build deps ──────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /venv && \
    /venv/bin/pip install --upgrade pip && \
    /venv/bin/pip install --no-cache-dir -r requirements.txt


# ── Stage 2: runtime ─────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Non-root user for security
RUN useradd -m -u 1000 trader

COPY --from=builder --chown=trader:trader /venv /venv

# Copy app files explicitly — never COPY . . to avoid leaking .env/creds
COPY --chown=trader:trader main.py             .
COPY --chown=trader:trader config.py           .
COPY --chown=trader:trader smartapi_loader.py  .
COPY --chown=trader:trader websocket_manager.py .
COPY --chown=trader:trader candle_store.py     .
COPY --chown=trader:trader indicators.py       .
COPY --chown=trader:trader heatmap.html        .

# Ship default watchlist — entrypoint copies to /data on first boot
COPY --chown=trader:trader watchlist.csv       ./watchlist_default.csv

# Fly mounts the persistent volume at /data
RUN mkdir -p /data && chown trader:trader /data

USER trader

ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WATCHLIST_PATH=/data/watchlist.csv \
    SCRIP_MASTER_PATH=/data/scrip_master.json

EXPOSE 8000

COPY --chown=trader:trader entrypoint.sh .
RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]