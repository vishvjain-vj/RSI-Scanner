# ============================================================
# Dockerfile — RSI Scanner (Render Optimized Architecture)
# ============================================================

# ── Stage 1: Build Dependencies ──────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /venv && \
    /venv/bin/pip install --upgrade pip && \
    /venv/bin/pip install --no-cache-dir -r requirements.txt


# ── Stage 2: Runtime ─────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Non-root user for security
RUN useradd -m -u 1000 trader

COPY --from=builder --chown=trader:trader /venv /venv

# Copy app files explicitly
COPY --chown=trader:trader main.py             .
COPY --chown=trader:trader config.py           .
COPY --chown=trader:trader smartapi_loader.py  .
COPY --chown=trader:trader websocket_manager.py .
COPY --chown=trader:trader candle_store.py     .
COPY --chown=trader:trader indicators.py       .
COPY --chown=trader:trader heatmap.html        .
COPY --chown=trader:trader watchlist.csv       ./watchlist_default.csv
COPY --chown=trader:trader entrypoint.sh       .

# 🔑 FIX: Grant execute rights while still root so it never gets blocked
RUN chmod +x entrypoint.sh

# 🔑 RENDER FREE TIER FIX: Create local data folder instead of a system mount 
RUN mkdir -p /app/data && chown -R trader:trader /app/data

USER trader

# 🔑 UPDATE ENVS: Point paths inside the app directory
ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WATCHLIST_PATH=/app/data/watchlist.csv \
    SCRIP_MASTER_PATH=/app/data/scrip_master.json

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]