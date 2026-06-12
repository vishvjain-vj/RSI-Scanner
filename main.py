import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
from flask import Flask, request, jsonify
import threading
import sys
import os
import time
import json
import urllib.request
import pandas as pd
from datetime import datetime
from logzero import logger

app = Flask(__name__)

from smartapi_loader import login, get_token, fetch_smartapi
from websocket_manager import MarketWebSocketManager
from candle_store import CandleStore
from indicators import calculate_rsi

WATCHLIST_FILE    = os.environ.get("WATCHLIST_PATH", "watchlist.csv")
SCRIP_MASTER_FILE = os.environ.get("SCRIP_MASTER_PATH", "scrip_master.json")
SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

# 🎯 UPGRADED: Fetches ~800-1000 historic candles per timeframe for accurate RSI
DAYS_BACK_MAP = {
    "1m": 4,       # 4 days * 375 candles = ~1,500 candles
    "5m": 15,      # 15 days * 75 candles = ~1,125 candles
    "15m": 40,     # 40 days * 25 candles = ~1,000 candles
    "30m": 80,     # 80 days * 12.5 candles = ~1,000 candles
    "1hr": 160,    # 160 days * 6.25 candles = ~1,000 candles
    "1D": 1000     # 1000 days * 1 candle = 1,000 candles
}

stream_node = {
    "instance": None, "session": None, "token_watchlist": {}
}

cached_scrip_list = []

def load_scrip_master():
    global cached_scrip_list
    if cached_scrip_list: return cached_scrip_list

    if os.path.exists(SCRIP_MASTER_FILE):
        try:
            with open(SCRIP_MASTER_FILE, 'r') as f:
                cached_scrip_list = json.load(f)
                if cached_scrip_list: return cached_scrip_list
        except Exception: pass
    
    logger.info("📥 Downloading Angel One Master Directory...")
    try:
        req = urllib.request.Request(SCRIP_MASTER_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            raw_data = json.loads(response.read().decode('utf-8'))
            nse_equities = [
                {"symbol": item.get("symbol"), "token": item.get("token"), "name": item.get("name")}
                for item in raw_data if item.get("exch_seg") == "NSE" and item.get("instrumenttype") == ""
            ]
            with open(SCRIP_MASTER_FILE, 'w') as f:
                json.dump(nse_equities, f)
            
            cached_scrip_list = nse_equities
            return cached_scrip_list
    except Exception as e:
        logger.error(f"❌ Failed downloading scrip master list: {e}")
        return []

def init_watchlist_tokens() -> dict:
    if not os.path.exists(WATCHLIST_FILE):
        logger.warning(f"⚠️ {WATCHLIST_FILE} missing. Creating template.")
        pd.DataFrame({"symbol": ["RELIANCE", "SBIN"]}).to_csv(WATCHLIST_FILE, index=False)

    df = pd.read_csv(WATCHLIST_FILE)
    symbols = df['symbol'].dropna().astype(str).tolist()
    
    token_map = {}
    cleaned_symbols = []
    csv_needs_update = False

    for sym in symbols:
        clean_sym = sym.upper().replace("-EQ", "").strip()
        if clean_sym != sym: csv_needs_update = True
            
        try:
            token = get_token(clean_sym)
            if token:
                token_map[token] = clean_sym
                cleaned_symbols.append(clean_sym)
        except Exception as ex:
            logger.error(f"❌ Could not resolve token for {clean_sym}: {ex}")
            
    if csv_needs_update:
        logger.info(f"🧹 Auto-cleaning {WATCHLIST_FILE}...")
        pd.DataFrame({"symbol": cleaned_symbols}).to_csv(WATCHLIST_FILE, index=False)
        
    return token_map

def start_signal_engine():
    global stream_node
    threading.Thread(target=load_scrip_master, daemon=True).start()

    while True:
        try:
            for cache_file in ["../session_cache.json", "session_cache.json"]:
                if os.path.exists(cache_file):
                    try: os.remove(cache_file)
                    except: pass

            token_watchlist = init_watchlist_tokens()
            if not token_watchlist: return
            stream_node["token_watchlist"] = token_watchlist

            smartapi_session = login()
            if not smartapi_session: return
            stream_node["session"] = smartapi_session

            stream = MarketWebSocketManager(smartapi_obj=smartapi_session)
            stream_node["instance"] = stream
            
            while True:
                time.sleep(1)
                if hasattr(stream, 'ws') and stream.ws and hasattr(stream.ws, 'ws') and stream.ws.ws is None:
                    break
        except KeyboardInterrupt:
            try: stream.stop_stream()
            except: pass
            break
        except Exception as crash_error:
            time.sleep(10)

def fetch_history_with_retry(session, token, symbol, angel_interval, days_back, max_retries=5):
    """Safely paced background fetch with capped backoff to prevent system freezes."""
    backoff_delay = 0.5  
    
    for attempt in range(1, max_retries + 1):
        try:
            # STRICT rate-limit guard (AngelOne allows 3 requests per second max)
            time.sleep(0.65) 
            
            history_df = fetch_smartapi(session, token, angel_interval, days_back)
            
            # SUCCESS: Data found and parsing succeeded
            if history_df is not None and not history_df.empty:
                return history_df
                
            # If API status is SUCCESS but data array is completely empty [] 
            # (Common during weekends or holiday edge cases), don't waste time retrying 5 times.
            logger.warning(f"⚠️ Empty historical data returned for {symbol} (Attempt {attempt}/{max_retries})")
            
        except Exception as e:
            err_msg = str(e)
            if any(x in err_msg for x in ["access rate", "Access denied", "501"]):
                logger.warning(f"🚨 Rate limit hit for {symbol}. Backing off for {backoff_delay}s...")
            else:
                logger.error(f"❌ Structural error fetching {symbol}: {err_msg}")
                # If it's a code error or an invalid token, retrying won't fix it. Exit early.
                if "Invalid Token" in err_msg or "invalid signature" in err_msg:
                    break
                
        # Handle backoff sleep execution
        time.sleep(backoff_delay)
        
        # 🎯 THE FIX: Multiply backoff but CAP IT at 4 seconds max so the background queue never freezes!
        backoff_delay = min(backoff_delay * 2, 4.0) 
        
    return None

def background_history_worker(tf, watchlist_items, session, stream, angel_interval, days_back):
    """Runs completely in the background so the UI doesn't freeze!"""
    logger.info(f"🌐 Background fetch started for {len(watchlist_items)} tokens...")
    for token, symbol in watchlist_items:
        store = stream.store_matrix[tf].get(token)
        if not store: continue
        
        history_df = fetch_history_with_retry(session, token, symbol, angel_interval, days_back)
        
        if history_df is not None and not history_df.empty:
            store.initialize_history(history_df)
            store.latest_rsi = calculate_rsi(store.history, period=14)
            store.latest_price = float(store.history['close'].iloc[-1])
            store.fetch_status = "READY"
        else:
            store.latest_rsi = None
            store.latest_price = 0.0
            store.fetch_status = "ERROR" # UI will render a red failure card
            
    stream.matrix_timestamps[tf] = time.time()
    logger.info(f"🏁 Background fetch complete for {tf}")


# =====================================================================
# 🌐 FLASK LOCAL INTERFACES & CONTROL ENDPOINTS
# =====================================================================

@app.route('/health')
def health():
    """
    Fly.io health check endpoint.
    Fly waits for 200 here before routing any traffic — prevents the 503
    on cold start that was happening on Render.
    Returns 503 while the engine thread is still logging in to Angel One.
    """
    if stream_node["instance"] is None:
        return jsonify({"status": "starting", "message": "Engine logging in to Angel One..."}), 503
    return jsonify({"status": "ready"}), 200


@app.route('/')
def serve_dashboard():
    try:
        with open('heatmap.html', 'r') as f:
            return f.read()
    except FileNotFoundError:
        return "heatmap.html file missing from execution folder.", 404

@app.route('/search_ticker', methods=['GET'])
def search_ticker():
    query = request.args.get('q', '').strip().upper()
    if not query or len(query) < 2: return jsonify([])

    scrip_list = load_scrip_master()
    suggestions = []
    
    for item in scrip_list:
        raw_symbol = item.get("symbol", "").upper()
        clean_symbol = raw_symbol.replace("-EQ", "")
        name = item.get("name", "").upper()
        
        if query in clean_symbol or query in name:
            if clean_symbol not in suggestions:
                suggestions.append(clean_symbol)
            if len(suggestions) >= 10: 
                break
    return jsonify(suggestions)

@app.route('/add_ticker', methods=['POST'])
def add_ticker():
    raw_input = request.json.get('symbol', '').strip().upper()
    if not raw_input: return jsonify({"status": "error", "message": "Invalid symbol input."}), 400

    symbol = raw_input.replace("-EQ", "")
    scrip_list = load_scrip_master()
    token = None
    for item in scrip_list:
        item_sym = item.get("symbol", "").upper()
        if item_sym == symbol or item_sym == f"{symbol}-EQ":
            token = str(item.get("token"))
            break

    if not token: return jsonify({"status": "error", "message": f"Ticker '{symbol}' not found in AngelOne registry."}), 404

    watchlist = stream_node["token_watchlist"]
    if token in watchlist: return jsonify({"status": "error", "message": f"'{symbol}' is already running."}), 400

    try:
        if os.path.exists(WATCHLIST_FILE): df = pd.read_csv(WATCHLIST_FILE)
        else: df = pd.DataFrame(columns=["symbol"])
        if symbol not in df['symbol'].astype(str).str.upper().values:
            new_row = pd.DataFrame({"symbol": [symbol]})
            df = pd.concat([df, new_row], ignore_index=True)
            df.to_csv(WATCHLIST_FILE, index=False)
    except Exception as e: logger.error(f"Failed to append ticker to CSV: {e}")

    watchlist[token] = symbol
    stream = stream_node["instance"]
    session = stream_node["session"]
    
    if stream and stream.active_timeframe:
        active_tf = stream.active_timeframe
        interval_map = {"1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE", "30m": "THIRTY_MINUTE", "1hr": "ONE_HOUR", "1D": "ONE_DAY"}
        angel_interval = interval_map.get(active_tf, "FIVE_MINUTE")
        days_back = DAYS_BACK_MAP.get(active_tf, 5)

        store = CandleStore(token=token, symbol=symbol, timeframe=active_tf, max_buffer=1500)
        history_df = fetch_history_with_retry(session, token, symbol, angel_interval, days_back)
        
        if history_df is not None and not history_df.empty:
            store.initialize_history(history_df)
            store.latest_rsi = calculate_rsi(store.history, period=14)
            store.latest_price = float(store.history['close'].iloc[-1])
            store.fetch_status = "READY"
        else:
            store.latest_rsi = None
            store.latest_price = 0.0
            store.fetch_status = "ERROR"

        stream.store_matrix[active_tf][token] = store

        if hasattr(stream, 'ws') and stream.ws:
            try: stream.ws.subscribe(stream.correlation_id, mode=2, token_list=[{"exchangeType": 1, "tokens": [token]}])
            except Exception as ex: logger.error(f"Runtime socket pipeline insertion failed: {ex}")

    return jsonify({"status": "success", "message": f"Added {symbol}!"})

@app.route('/remove_ticker', methods=['POST'])
def remove_ticker():
    symbol = request.json.get('symbol', '').strip().upper()
    if not symbol: return jsonify({"status": "error", "message": "Invalid symbol."}), 400

    watchlist = stream_node["token_watchlist"]
    token_to_remove = None
    
    for token, sym in list(watchlist.items()):
        if sym == symbol:
            token_to_remove = token
            del watchlist[token]
            break

    if not token_to_remove: return jsonify({"status": "error", "message": f"{symbol} not found."}), 404

    try:
        if os.path.exists(WATCHLIST_FILE):
            df = pd.read_csv(WATCHLIST_FILE)
            df = df[df['symbol'].astype(str).str.upper() != symbol]
            df.to_csv(WATCHLIST_FILE, index=False)
    except Exception as e: pass

    stream = stream_node["instance"]
    if stream:
        for tf in stream.store_matrix:
            if token_to_remove in stream.store_matrix[tf]: stream.store_matrix[tf].pop(token_to_remove, None)
        if hasattr(stream, 'ws') and stream.ws:
            try: stream.ws.unsubscribe(stream.correlation_id, mode=2, token_list=[{"exchangeType": 1, "tokens": [token_to_remove]}])
            except Exception as ex: pass

    return jsonify({"status": "success", "message": f"{symbol} completely removed."})


@app.route('/set_timeframe', methods=['POST'])
def set_timeframe():
    new_tf = request.json.get('timeframe')
    stream = stream_node["instance"]
    session = stream_node["session"]
    watchlist = stream_node["token_watchlist"]
    
    if not stream:
        # Engine still logging in — return 202 so the frontend retries
        # instead of showing a hard error. Fly health check prevents traffic
        # until /health returns 200, but direct API calls during startup hit this.
        return jsonify({"status": "loading", "message": "Engine starting up — retrying in 5s..."}), 202

    if new_tf in stream.store_matrix:
        is_first_activation = (stream.active_timeframe is None)
        stream.active_timeframe = new_tf
        
        if not stream.store_matrix[new_tf]:
            logger.info(f"🌐 Initializing asynchronous history fetch for timeframe: {new_tf}")
            interval_map = {"1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE", "30m": "THIRTY_MINUTE", "1hr": "ONE_HOUR", "1D": "ONE_DAY"}
            angel_interval = interval_map.get(new_tf, "FIVE_MINUTE")
            days_back = DAYS_BACK_MAP.get(new_tf, 5)

            # 🎯 Instantly create placeholders for the frontend to show "LOADING"
            for token, symbol in list(watchlist.items()):
                store = CandleStore(token=token, symbol=symbol, timeframe=new_tf, max_buffer=1500)
                store.fetch_status = "LOADING" 
                stream.store_matrix[new_tf][token] = store
                
            # 🎯 Spin up the background worker thread so the web request returns instantly!
            t = threading.Thread(target=background_history_worker, args=(new_tf, list(watchlist.items()), session, stream, angel_interval, days_back))
            t.daemon = True
            t.start()

        if is_first_activation or not hasattr(stream, 'ws') or stream.ws is None:
            stream.start_stream(token_list=list(watchlist.keys()))

        return jsonify({"status": "success", "active": stream.active_timeframe})
        
    return jsonify({"status": "error", "message": "Unsupported interval key"}), 400

@app.route('/get_dashboard', methods=['GET'])
def get_dashboard():
    stream = stream_node["instance"]
    if not stream or stream.active_timeframe is None:
        return jsonify({"active_timeframe": None, "data": {}})
        
    active_tf = stream.active_timeframe
    dashboard_data = {}
    
    if active_tf in stream.store_matrix and stream.store_matrix[active_tf]:
        for token, store in list(stream.store_matrix[active_tf].items()):
            price = getattr(store, 'latest_price', 0.0)
            rsi   = getattr(store, 'latest_rsi', None)
            # Fetch the status we set dynamically
            status = getattr(store, 'fetch_status', 'READY')
            
            # 🔥 NEW: Extract ATP from the store safely
            atp = getattr(store, 'latest_atp', None)

            if price == 0.0 and not store.history.empty:
                price = float(store.history['close'].iloc[-1])

            dashboard_data[store.symbol] = {
                "price": price,
                "rsi":   rsi, 
                "status": status,
                "atp": atp        # 🔥 NEW: Add ATP to JSON payload
            }
        
    return jsonify({"active_timeframe": active_tf, "data": dashboard_data})

@app.route('/health')
def health():
    if stream_node["instance"] is None:
        return jsonify({"status": "starting"}), 503  # Render retries
    return jsonify({"status": "ready"}), 200

# 🔥 NEW: Move the thread outside so Gunicorn triggers it immediately!
engine_thread = threading.Thread(target=start_signal_engine)
engine_thread.daemon = True
engine_thread.start()

if __name__ == "__main__":
    logger.info("🚀 Launching Web on Localhost...")
    app.run(host='0.0.0.0', port=8000, debug=False)