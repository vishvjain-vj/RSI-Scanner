import sys
import os
import logging
import json
import threading
from datetime import datetime, timedelta
from config import get_client_id
from logzero import logger
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from indicators import calculate_rsi

# Suppress underlying socket protocol debugging logs
logging.getLogger("websocket").setLevel(logging.WARNING)

class MarketWebSocketManager:
    def __init__(self, smartapi_obj):
        self.smartApi = smartapi_obj
        self.obj = smartapi_obj  
        
        self.correlation_id = "trader_stream_v2"

        # None = no timeframe selected yet. main.py also sets this to None after
        # instantiation, but setting it here means the class is safe to use
        # standalone without relying on the caller to patch it immediately after.
        self.active_timeframe = None
        self.cache_expiry_seconds = 3600
        
        self.store_matrix = {
            "1m": {}, "5m": {}, "15m": {}, "30m": {}, "1hr": {}, "1D": {}
        }
        self.matrix_timestamps = {tf: 0.0 for tf in self.store_matrix.keys()}
        self.matrix_lock = threading.Lock()
        
    def attach_stores(self, store_map: dict):
        self.store_map = store_map

    def start_stream(self, token_list: list):
        """Initializes the background worker thread for live streaming."""
        if not self.obj:
            logger.error("❌ Cannot start stream: SmartAPI session is invalid.")
            return

        self.tokens_to_subscribe = token_list
        
        # Instantiate V2 client extracting tokens generated during smartapi_loader login
        self.ws = SmartWebSocketV2(
            auth_token=self.obj.access_token,
            api_key=self.obj.api_key,
            client_code=get_client_id(),
            feed_token=self.obj.getfeedToken()
        )

        # Map internal connection methods to SDK listeners
        self.ws.on_open = self._on_open
        self.ws.on_data = self._on_data
        self.ws.on_error = self._on_error
        self.ws.on_close = self._on_close

        logger.info("🛰️ Launching AngelOne Live Websocket Worker Thread...")
        self.ws.connect()

    def _on_open(self, wsapp):
        logger.info("✅ Live Market Stream Link Established.")
        
        # Construct exact SmartAPI format payload
        subscription_payload = [
            {
                "exchangeType": 1,  # 1 = NSE Equity
                "tokens": self.tokens_to_subscribe
            }
        ]
        
        # Action code: 1 = Subscribe, Mode: 2 = Quote Mode (includes High/Low/Volume)
        self.ws.subscribe(self.correlation_id, mode=2, token_list=subscription_payload)
        logger.info(f"📥 Token subscription request queued for: {self.tokens_to_subscribe}")

    def _update_local_dashboard(self):
        """Saves the current market state to a JSON file for the HTML heatmap to read."""
        import json
        try:
            with open("dashboard_data.json", "w") as f:
                json.dump(getattr(self, 'dashboard_state', {}), f)
        except Exception as e:
            logger.error(f"Failed to update dashboard file: {e}")

    def _on_data(self, wsapp, message):
        """
        Processes live ticks, routes data ONLY to the active timeframe,
        and wipes out inactive timeframes that have crossed the 1-hour expiry window.
        """
        import time
        from datetime import datetime
        from indicators import calculate_rsi 

        try:
            # 1. Basic validation of incoming WebSocket message
            if 'token' not in message or 'last_traded_price' not in message:
                return
                
            token = str(message['token'])
            ltp = float(message['last_traded_price']) / 100.0  
            volume = int(message.get('last_traded_quantity', 0))
            
            # 🔥 NEW: Capture ATP from Angel One's payload
            atp = None
            if 'average_traded_price' in message and message['average_traded_price'] > 0:
                atp = float(message['average_traded_price']) / 100.0
            elif 'ap' in message:
                atp = float(message['ap'])
            
            tick_time = datetime.fromtimestamp(message['exchange_timestamp'] / 1000.0) if 'exchange_timestamp' in message else datetime.now()

            # Acquire lock to protect operations on shared store_matrix maps
            with self.matrix_lock:
                # 2. ⏳ HOUSEKEEPING: Check and wipe expired inactive timeframes
                current_time = time.time()
                for tf in list(self.store_matrix.keys()):
                    # Skip the currently active timeframe
                    if tf == self.active_timeframe:
                        continue
                    
                    # If an inactive timeframe has data, check its age
                    if self.matrix_timestamps[tf] > 0:
                        age = current_time - self.matrix_timestamps[tf]
                        if age >= self.cache_expiry_seconds:
                            print(f"⏰ Cache Expired! Clearing data for inactive timeframe: {tf}")
                            self.store_matrix[tf].clear()      # Wipes the data empty
                            self.matrix_timestamps[tf] = 0.0   # Resets the timer completely

                # 3. 🎯 LIVE ROUTING: Only process the tick for the currently ACTIVE timeframe
                active_tf = self.active_timeframe

                # Guard: active_tf is None until user clicks a timeframe in the UI
                if active_tf is None:
                    return

                if active_tf in self.store_matrix and token in self.store_matrix[active_tf]:
                    store = self.store_matrix[active_tf][token]

                    # Process the tick inside the agnostic CandleStore
                    is_closed, completed_candle = store.process_tick(tick_time, ltp, volume)

                    # Always keep latest_price current
                    store.latest_price = ltp
                    
                    # 🔥 NEW: Save the ATP to the store so the API can read it
                    if atp is not None:
                        store.latest_atp = atp

                    try:
                        import pandas as pd
                        # Pull out your existing list of historical close prices
                        closed_closes = store.history['close'].tolist() if store.history is not None else []
    
                        # 🛡️ THE DATA GUARD: If historical fetch failed completely (or has fewer than 100 candles), 
                        # STOP calculating false RSIs. Keep it None until data resolves.
                        if len(closed_closes) >= 100:
                            closed_closes.append(ltp)
                            live_df = pd.DataFrame({'close': closed_closes})
                            live_rsi = calculate_rsi(live_df, period=14)
        
                        if live_rsi is not None:
                            store.latest_rsi = live_rsi
                            self.matrix_timestamps[active_tf] = time.time()
                        else:
                            # History cache is empty or incomplete! Force RSI to reflect loading/error state
                            store.latest_rsi = None
        
                    except Exception as rsi_err:
                        pass

        except Exception as e:
            print(f"⚠️ Error in active websocket tick parsing: {e}")

    def _on_error(self, *args):
        """
        Catches any connection drop, handshake failure, or socket exceptions.
        """
        error = args[-1] if args else "Unknown socket issue"
        logger.error(f"⚠️ Websocket Interface Error caught: {error}")

    def _on_close(self, *args):
        """
        Fires automatically when the AngelOne streaming servers sever the link.
        """
        logger.warning("🔌 WebSocket link closed by server or operator request.")

    def stop_stream(self):
        """Gracefully disconnects and shuts down the websocket client thread."""
        if self.ws:
            logger.info("🔌 Terminating WebSocket Stream gracefully...")
            self.ws.close_connection()