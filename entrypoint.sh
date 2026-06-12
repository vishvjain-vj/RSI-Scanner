#!/bin/sh
# ============================================================
# entrypoint.sh
# Handles first-boot volume population then starts the app.
# On subsequent boots, the user's saved watchlist is kept.
# ============================================================

set -e

WATCHLIST="/data/watchlist.csv"
SCRIP_MASTER="/data/scrip_master.json"
DEFAULT_WATCHLIST="./watchlist_default.csv"

# First boot — copy default watchlist to persistent volume
if [ ! -f "$WATCHLIST" ]; then
    echo "[entrypoint] First boot — copying default watchlist to /data/"
    cp "$DEFAULT_WATCHLIST" "$WATCHLIST"
else
    LINES=$(wc -l < "$WATCHLIST")
    echo "[entrypoint] Persistent watchlist found — ${LINES} lines (tickers preserved)"
fi

# scrip_master.json is downloaded at runtime by main.py if missing
# Pre-existing one on the volume is reused (saves startup time)
if [ -f "$SCRIP_MASTER" ]; then
    echo "[entrypoint] Scrip master cache found on volume — will reuse"
else
    echo "[entrypoint] No scrip master cache — will download fresh on startup"
fi

echo "[entrypoint] Starting RSI Scanner on port 8000..."
exec gunicorn main:app \
    --bind 0.0.0.0:8000 \
    --workers 1 \
    --threads 4 \
    --timeout 120 \
    --keep-alive 5 \
    --log-level info