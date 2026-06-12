import pandas as pd
from datetime import datetime, timedelta
from logzero import logger

class CandleStore:
    def __init__(self, symbol: str, token: str, timeframe: str = "5m", max_buffer: int = 100):
        self.symbol = symbol
        self.token = token
        self.timeframe = timeframe  
        self.max_buffer = max_buffer
        
        # Use 'datetime' tracking consistently
        self.history = pd.DataFrame(columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
        
        self.candle_duration_minutes = self._get_duration_minutes(timeframe)
        self.timeframe_minutes = self.candle_duration_minutes
        
        # 🎯 FIX 2: Initialize both tracking variables cleanly to avoid AttributeErrors
        self.current_candle = None
        self.current_candle_start = None 

    def _get_duration_minutes(self, timeframe: str) -> int:
        mapping = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1hr": 60, "1D": 1440}
        return mapping.get(timeframe, 5) 

    def initialize_history(self, historical_df: pd.DataFrame):
        """Loads pre-fetched historical candles and normalizes column headers."""
        if historical_df is not None and not historical_df.empty:
            df_subset = historical_df.tail(self.max_buffer).copy()
            
            if df_subset.index.name == 'datetime' or isinstance(df_subset.index, pd.DatetimeIndex):
                df_subset.reset_index(inplace=True)
            
            # Normalize to lowercase
            df_subset.columns = [c.lower() for c in df_subset.columns]
            
            # 🎯 FIX 3: Safety mapping for various Angel One historical response shapes
            if 'timestamp' in df_subset.columns:
                df_subset.rename(columns={'timestamp': 'datetime'}, inplace=True)
            elif 'date' in df_subset.columns:
                df_subset.rename(columns={'date': 'datetime'}, inplace=True)
            
            required_cols = ['datetime', 'open', 'high', 'low', 'close', 'volume']
            
            # Ensure every column is accounted for
            for col in required_cols:
                if col not in df_subset.columns:
                    df_subset[col] = 0.0 if col != 'datetime' else pd.Timestamp.now()

            self.history = df_subset[required_cols].copy()
            logger.info(f"📥 [{self.symbol}] {self.timeframe_minutes}m History Ready: Loaded {len(self.history)} candles.")

    def _get_candle_start_time(self, tick_time: datetime) -> datetime:
        total_minutes = tick_time.hour * 60 + tick_time.minute
        floor_minutes = total_minutes - (total_minutes % self.timeframe_minutes)
        hour = floor_minutes // 60
        minute = floor_minutes % 60
        return tick_time.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def process_tick(self, tick_time: datetime, ltp: float, volume: int = 0):
        tick_candle_start = self._get_candle_start_time(tick_time)

        # First tick received by the system 
        if self.current_candle_start is None:
            self.current_candle_start = tick_candle_start
            self.current_candle = {
                'datetime': tick_candle_start, 
                'open': ltp, 'high': ltp, 'low': ltp, 'close': ltp, 'volume': volume
            }
            return False, None

        # Immediate boundary cross detected
        if tick_candle_start > self.current_candle_start:
            completed_candle = self.current_candle.copy()
            self._add_to_history(completed_candle)
            
            self.current_candle_start = tick_candle_start
            self.current_candle = {
                'datetime': tick_candle_start, 
                'open': ltp, 'high': ltp, 'low': ltp, 'close': ltp, 'volume': volume
            }
            return True, completed_candle

        # Tick is within the same window
        else:
            self.current_candle['high'] = max(self.current_candle['high'], ltp)
            self.current_candle['low'] = min(self.current_candle['low'], ltp)
            self.current_candle['close'] = ltp
            self.current_candle['volume'] += volume
            return False, None

    def _add_to_history(self, completed_candle: dict):
        new_row = pd.DataFrame([completed_candle])
        if self.history is None or self.history.empty:
            self.history = new_row
        else:
            self.history = pd.concat([self.history, new_row], ignore_index=True)

        if len(self.history) > self.max_buffer:
            self.history = self.history.iloc[-self.max_buffer:].reset_index(drop=True)

    def get_history_df(self) -> pd.DataFrame:
        return self.history