# ============================================================
# data/smartapi_loader.py
# ============================================================

from curses import raw
import os, json, time, requests, pyotp
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

try:
    from SmartApi import SmartConnect
    SMARTAPI_AVAILABLE = True
except ImportError:
    SMARTAPI_AVAILABLE = False
    print("[loader] smartapi-python not installed.")
    print("         Run: pip install smartapi-python pyotp logzero\n")

from config import get_api_key, get_client_id, get_password, get_totp_secret

# ─────────────────────────────────────────────────────────────
SCRIP_MASTER_URL   = (
    "https://margincalculator.angelbroking.com/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)
SESSION_CACHE_FILE = "session_cache.json"

CHUNK_DAYS = {
    "ONE_MINUTE": 7, "FIVE_MINUTE": 25, "FIFTEEN_MINUTE": 50,
    "THIRTY_MINUTE": 50, "ONE_HOUR": 90, "ONE_DAY": 365,
}
MAX_LOOKBACK = {
    "ONE_MINUTE": 30,  "FIVE_MINUTE": 100, "FIFTEEN_MINUTE": 200,
    "THIRTY_MINUTE": 200, "ONE_HOUR": 400,  "ONE_DAY": 2000,
}

# ─────────────────────────────────────────────────────────────
# SESSION
# ─────────────────────────────────────────────────────────────

def _load_cached_session():
    if not os.path.exists(SESSION_CACHE_FILE):
        return None
    try:
        with open(SESSION_CACHE_FILE) as f:
            c = json.load(f)
        return c if c.get("date") == datetime.now().strftime("%Y-%m-%d") else None
    except Exception:
        return None

def _save_session(auth, refresh, feed):
    with open(SESSION_CACHE_FILE, "w") as f:
        json.dump({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "auth_token": auth,
            "refresh_token": refresh,
            "feed_token": feed,
        }, f, indent=2)

def login():
    """
    Login to AngelOne SmartAPI.
    Credentials are read from environment variables via config.py.
    Returns authenticated SmartConnect object, or None on failure.

    Session is cached in session_cache.json for the day —
    re-login only happens once per calendar day.
    """
    if not SMARTAPI_AVAILABLE:
        print("[session] SmartAPI not installed — cannot login.")
        return None

    # Read credentials from environment variables
    try:
        api_key     = get_api_key()
        client_id   = get_client_id()
        password    = get_password()
        totp_secret = get_totp_secret()
    except EnvironmentError as e:
        print(e)
        return None

    obj = SmartConnect(api_key=api_key)

    # Reuse today's cached session if available
    cached = _load_cached_session()
    if cached:
        print("[session] Reusing today's cached session ✓")
        obj.setAccessToken(cached["auth_token"])
        obj.setRefreshToken(cached["refresh_token"])
        obj.setFeedToken(cached["feed_token"])
        return obj

    # Fresh login
    print("[session] Logging in to AngelOne SmartAPI...")
    try:
        totp = pyotp.TOTP(totp_secret).now()
    except Exception as e:
        print(f"[session] ✗ TOTP generation failed: {e}")
        print("          Check ANGEL_TOTP_SECRET — must be the 32-char")
        print("          base32 string from smartapi.angelbroking.com/enable-totp")
        return None

    try:
        resp = obj.generateSession(client_id, password, totp)
    except Exception as e:
        print(f"[session] ✗ Login request failed: {e}")
        return None

    if not resp or resp.get("status") is False:
        msg = resp.get("message", "Unknown") if resp else "No response"
        print(f"[session] ✗ Rejected by AngelOne: {msg}")
        return None

    auth    = resp["data"]["jwtToken"]
    refresh = resp["data"]["refreshToken"]
    feed    = obj.getfeedToken()
    _save_session(auth, refresh, feed)
    print("[session] Login successful ✓  Session cached for today.")
    return obj

# ─────────────────────────────────────────────────────────────
# TOKEN LOOKUP
# ─────────────────────────────────────────────────────────────

_scrip_df = None

def _get_scrip_master():
    global _scrip_df
    if _scrip_df is not None:
        return _scrip_df
    print("[instruments] Downloading instrument master (once per session)...")
    resp = requests.get(SCRIP_MASTER_URL, timeout=30)
    resp.raise_for_status()
    _scrip_df = pd.DataFrame(resp.json())
    print(f"[instruments] {len(_scrip_df):,} instruments loaded ✓")
    return _scrip_df

def get_token(nse_symbol: str) -> str:
    """
    Get AngelOne symbol token for any NSE equity.
    Supports standard -EQ equities as well as special segments like -BE and -BZ series.
    """
    symbol = nse_symbol.upper().replace(".NS","").replace(".BO","").strip()
    df     = _get_scrip_master()

    # Primary match: Checks for "SYMBOL-EQ" OR matches the exact symbol string directly (handles -BE/-BZ)
    m = df[(df["exch_seg"]=="NSE") & ((df["symbol"].str.upper() == f"{symbol}-EQ") | (df["symbol"].str.upper() == symbol))]
    if not m.empty:
        token = str(m.iloc[0]["token"])
        print(f"[token] {symbol} -> {token} ✓")
        return token

    # Fallback: match by company name field
    m2 = df[(df["exch_seg"]=="NSE") & (df["name"].str.upper()==symbol)]
    if not m2.empty:
        token = str(m2.iloc[0]["token"])
        print(f"[token] {symbol} -> {token} (matched by name) ✓")
        return token

    raise ValueError(
        f"'{symbol}' not found in AngelOne instrument master.\n"
        f"Use exact NSE trading symbol e.g. 'RELIANCE', 'TCS'."
    )

# ─────────────────────────────────────────────────────────────
# CHUNKED HISTORICAL FETCH
# ─────────────────────────────────────────────────────────────

def _fetch_chunk(obj, token, interval, from_dt, to_dt):
    params = {
        "exchange":    "NSE",
        "symboltoken": token,
        "interval":    interval,
        "fromdate":    from_dt.strftime("%Y-%m-%d %H:%M"),
        "todate":      to_dt.strftime("%Y-%m-%d %H:%M"),
    }
    try:
        resp = obj.getCandleData(params)
    except Exception as e:
        print(f"    [chunk] API error: {e}")
        return None

    if not resp or resp.get("status") is False:
        return None

    raw = resp.get("data")
    if not raw:
        return None

    # 1. Build the DataFrame normally
    df = pd.DataFrame(raw, columns=["datetime", "open", "high", "low", "close", "volume"])

    # 2. BULLETPROOF FIX FOR PANDAS 3.0+: 
    # Extract, drop the restricted 'str' column, and insert as a native datetime series
    parsed_timestamps = pd.to_datetime(df["datetime"])
    df = df.drop(columns=["datetime"])
    df.insert(0, "datetime", parsed_timestamps)
    
    df = df.set_index("datetime")
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.dropna(subset=["close"]) if not df.empty else None

def fetch_smartapi(obj, token: str, interval: str, days: int):
    """
    Fetch candles from SmartAPI across safe date chunks.

    Parameters
    ----------
    obj      : SmartConnect from login()
    token    : from get_token()
    interval : "ONE_MINUTE" | "FIVE_MINUTE" | "FIFTEEN_MINUTE" |
               "THIRTY_MINUTE" | "ONE_HOUR" | "ONE_DAY"
    days     : days of history to fetch (auto-capped to API limit)

    Returns
    -------
    pd.DataFrame with columns [open, high, low, close, volume]
    and a timezone-naive DatetimeIndex, or None if fetch failed.
    """
    actual     = min(days, MAX_LOOKBACK.get(interval, 60))
    chunk_size = CHUNK_DAYS.get(interval, 30)
    to_dt      = datetime.now()
    from_dt    = to_dt - timedelta(days=actual)

    # Build chunk windows
    chunks, cursor = [], from_dt
    while cursor < to_dt:
        end = min(cursor + timedelta(days=chunk_size), to_dt)
        chunks.append((cursor, end))
        cursor = end

    print(f"  [{interval}] {from_dt.date()} -> {to_dt.date()} | {len(chunks)} chunk(s)")

    all_dfs = []
    for i, (cf, ct) in enumerate(chunks, 1):
        df = _fetch_chunk(obj, token, interval, cf, ct)
        if df is not None:
            all_dfs.append(df)
            print(f"    chunk {i}/{len(chunks)}: {len(df)} rows ✓")
        else:
            print(f"    chunk {i}/{len(chunks)}: empty")
        if i < len(chunks):
            time.sleep(0.5)   # rate limit protection

    if not all_dfs:
        return None

    result = (
        pd.concat(all_dfs)
        .sort_index()
        .pipe(lambda d: d[~d.index.duplicated(keep="last")])
        .dropna(subset=["close"])
    )
    print(f"  [{interval}] Total: {len(result)} rows  "
          f"({result.index[0].date()} -> {result.index[-1].date()}) ✓")
    return result

# ─────────────────────────────────────────────────────────────
# YFINANCE — daily / weekly / monthly
# ─────────────────────────────────────────────────────────────

def _yfinance(symbol_ns: str, interval: str, start: str):
    """Fetch daily/weekly/monthly OHLCV from yfinance (unlimited history)."""
    try:
        df = yf.download(symbol_ns, interval=interval, start=start,
                         progress=False, auto_adjust=True)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df.dropna(subset=["close"])
    except Exception as e:
        print(f"  [yfinance] {e}")
        return None

# ─────────────────────────────────────────────────────────────
# MASTER LOADER
# ─────────────────────────────────────────────────────────────

def load_all_timeframes(
    nse_symbol: str,
    daily_start: str = "2018-01-01",
    include_intraday: bool = True,
) -> dict:
    """
    Load ALL timeframes for an NSE stock.

    Returns
    -------
    {
      '1m':      DataFrame | None   (30 days,  SmartAPI)
      '5m':      DataFrame | None   (100 days, SmartAPI)
      '15m':     DataFrame | None   (200 days, SmartAPI)
      '30m':     DataFrame | None   (200 days, SmartAPI)
      '1h':      DataFrame | None   (400 days, SmartAPI)
      '4h':      DataFrame | None   (resampled from 1h)
      'daily':   DataFrame | None   (unlimited, yfinance)
      'weekly':  DataFrame | None   (unlimited, yfinance)
      'monthly': DataFrame | None   (unlimited, yfinance)
    }

    All DataFrames:
      - Columns: open, high, low, close, volume  (all lowercase)
      - Index:   DatetimeIndex, timezone-naive
      - No NaN in close column

    Parameters
    ----------
    nse_symbol       : e.g. "RELIANCE" or "RELIANCE.NS" (both work)
    daily_start      : start date for daily/weekly/monthly data
    include_intraday : False = skip SmartAPI, only load yfinance data
    """
    symbol    = nse_symbol.upper().replace(".NS","").strip()
    symbol_ns = f"{symbol}.NS"
    result    = {}

    print(f"\n{'='*60}")
    print(f"  Loading data for {symbol}")
    print(f"{'='*60}")

    # ── Intraday via SmartAPI ─────────────────────────────────
    if include_intraday:
        print("\n[1/2] Intraday — AngelOne SmartAPI")
        obj = login()

        if obj:
            try:
                token = get_token(symbol)
            except ValueError as e:
                print(f"  ✗ {e}")
                obj = None

        if obj:
            intraday_plan = [
                ("1m",  "ONE_MINUTE",     30),
                ("5m",  "FIVE_MINUTE",    100),
                ("15m", "FIFTEEN_MINUTE", 200),
                ("30m", "THIRTY_MINUTE",  200),
                ("1h",  "ONE_HOUR",       400),
            ]
            for label, interval, days in intraday_plan:
                print(f"\n  -> {label}:")
                result[label] = fetch_smartapi(obj, token, interval, days)
                time.sleep(0.3)

            # 4h: resample 1h into 4h candles
            if result.get("1h") is not None:
                print("\n  -> 4h (resampled from 1h):")
                df4 = (
                    result["1h"]
                    .resample("4h")
                    .agg({"open":"first","high":"max",
                          "low":"min","close":"last","volume":"sum"})
                    .dropna(subset=["close"])
                )
                result["4h"] = df4 if not df4.empty else None
                if result["4h"] is not None:
                    print(f"    {len(result['4h'])} candles ✓")
        else:
            print("  SmartAPI unavailable — intraday data set to None")
            for label in ["1m","5m","15m","30m","1h","4h"]:
                result[label] = None
    else:
        for label in ["1m","5m","15m","30m","1h","4h"]:
            result[label] = None

    # ── Daily / Weekly / Monthly via yfinance ────────────────
    print("\n[2/2] Daily / Weekly / Monthly — yfinance")
    for label, interval in [("daily","1d"),("weekly","1wk"),("monthly","1mo")]:
        print(f"\n  -> {label}:")
        df = _yfinance(symbol_ns, interval, daily_start)
        result[label] = df
        if df is not None:
            print(f"    {len(df)} rows | "
                  f"{df.index[0].date()} -> {df.index[-1].date()} ✓")
        else:
            print(f"    No data returned ✗")

    _print_summary(symbol, result)
    return result


def _print_summary(symbol, result):
    order  = ["1m","5m","15m","30m","1h","4h","daily","weekly","monthly"]
    source = {
        "1m":"SmartAPI","5m":"SmartAPI","15m":"SmartAPI",
        "30m":"SmartAPI","1h":"SmartAPI","4h":"SmartAPI(1h->4h)",
        "daily":"yfinance","weekly":"yfinance","monthly":"yfinance",
    }
    print(f"\n{'─'*68}")
    print(f"  LOAD SUMMARY — {symbol}")
    print(f"{'─'*68}")
    print(f"  {'TF':<8} {'Source':<22} {'Rows':>6}  {'From':>12}  {'To':>12}")
    print(f"  {'─'*63}")
    for tf in order:
        df  = result.get(tf)
        src = source.get(tf, "?")
        if df is not None:
            print(f"  {tf:<8} {src:<22} {len(df):>6}  "
                  f"{str(df.index[0].date()):>12}  {str(df.index[-1].date()):>12}")
        else:
            print(f"  {tf:<8} {src:<22}   None")
    print(f"{'─'*68}\n")