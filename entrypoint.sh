#!/bin/sh
set -e

# Update paths to match the internal non-root workspace
WATCHLIST="/app/data/watchlist.csv"
SCRIP_MASTER="/app/data/scrip_master.json"
DEFAULT_WATCHLIST="./watchlist_default.csv"

# First boot — copy default watchlist to local volume
if [ ! -f "$WATCHLIST" ]; then
    echo "[entrypoint] Initializing data directory — copying default watchlist"
    cp "$DEFAULT_WATCHLIST" "$WATCHLIST"
else
    LINES=$(wc -l < "$WATCHLIST")
    echo "[entrypoint] Local watchlist found — ${LINES} lines preserved"
fi

if [ -f "$SCRIP_MASTER" ]; then
    echo "[entrypoint] Scrip master cache found — reusing"
else
    echo "[entrypoint] No scrip master cache — downloading fresh on startup"
fi

echo "[entrypoint] Starting RSI Scanner on port 8000..."
exec gunicorn main:app \
    --bind 0.0.0.0:8000 \
    --workers 1 \
    --threads 4 \
    --timeout 120 \
    --keep-alive 5 \
    --log-level info