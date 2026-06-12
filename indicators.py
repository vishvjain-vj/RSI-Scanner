import pandas as pd
import numpy as np
from typing import Optional


def calculate_rsi(df: pd.DataFrame, period: int = 7) -> Optional[float]:
    """
    Calculates Wilder's RSI matching TradingView's output exactly.

    Key differences from naive EWM approach:
      - Seeds avg_gain / avg_loss with simple mean of first `period` bars
        (matches TradingView's initialization, not pandas ewm default)
      - Applies Wilder's smoothing manually after the seed
        formula: avg = (prev_avg * (period-1) + current) / period
      - Returns None if insufficient data (not a fake 50.0)

    Parameters
    ----------
    df     : DataFrame with a 'close' column, oldest row first
    period : RSI lookback period (default 7 to match your strategy)

    Returns
    -------
    float  : RSI of the last fully closed candle
    None   : if fewer than period+1 rows available
    """
    if len(df) < period + 1:
        return None

    closes = df["close"].astype(float).values
    delta  = np.diff(closes)                      # length = len(closes) - 1

    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    # ── Seed: simple average of first `period` bars ──────────────────────────
    # This is the step that makes it match TradingView.
    # pandas ewm(adjust=False) seeds from bar 0 (just gain[0]),
    # TradingView seeds from the mean of bars 0..period-1.
    avg_gain = gain[:period].mean()
    avg_loss = loss[:period].mean()

    # ── Wilder's smoothing for every bar after the seed ───────────────────────
    for i in range(period, len(gain)):
        avg_gain = (avg_gain * (period - 1) + gain[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i]) / period

    # ── RSI ───────────────────────────────────────────────────────────────────
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1 + rs)), 2)