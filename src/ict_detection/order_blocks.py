"""
Order Block Detection Module
Identifies institutional buying and selling zones
"""

import pandas as pd
import numpy as np


class OrderBlockDetector:
    """Detects Order Blocks in price data"""
    
    def __init__(self, lookback=20):
        """
        Initialize Order Block detector
        
        Args:
            lookback (int): How many candles to look back for swing points
        """
        self.lookback = lookback
    
    def detect_bullish_ob(self, df):
        """
        Detect bullish Order Blocks
        
        A bullish OB is:
        - The last DOWN (bearish) candle before price makes a strong move UP
        - This is where institutions were buying (disguised as selling)
        - The high-low range of this candle becomes a support zone
        
        Args:
            df (pd.DataFrame): OHLCV data with swing points
            
        Returns:
            list: List of Order Block dictionaries
        """
        order_blocks = []
        
        # Need swing_low column (from swing detection)
        if 'swing_low' not in df.columns:
            print("⚠ Warning: swing_low column not found. Run swing detection first.")
            return order_blocks
        
        # Find swing lows
        swing_lows = df[df['swing_low']].index
        
        for swing_idx in swing_lows:
            swing_position = df.index.get_loc(swing_idx)
            
            # Look back from swing low to find last bearish candle
            for i in range(swing_position - 1, max(0, swing_position - self.lookback), -1):
                candle_open = df['open'].iloc[i]
                candle_close = df['close'].iloc[i]
                
                # Is it a bearish (down) candle?
                if candle_close < candle_open:
                    ob = {
                        'type': 'bullish',
                        'index': i,
                        'timestamp': df.index[i],
                        'high': df['high'].iloc[i],
                        'low': df['low'].iloc[i],
                        'open': candle_open,
                        'close': candle_close,
                        'related_swing': swing_idx,
                        'tested': False
                    }
                    order_blocks.append(ob)
                    break  # Found it, move to next swing
        
        return order_blocks
    
    def detect_bearish_ob(self, df):
        """
        Detect bearish Order Blocks
        
        A bearish OB is:
        - The last UP (bullish) candle before price makes a strong move DOWN
        - This is where institutions were selling (disguised as buying)
        - The high-low range of this candle becomes a resistance zone
        
        Args:
            df (pd.DataFrame): OHLCV data with swing points
            
        Returns:
            list: List of Order Block dictionaries
        """
        order_blocks = []
        
        # Need swing_high column
        if 'swing_high' not in df.columns:
            print("⚠ Warning: swing_high column not found. Run swing detection first.")
            return order_blocks
        
        # Find swing highs
        swing_highs = df[df['swing_high']].index
        
        for swing_idx in swing_highs:
            swing_position = df.index.get_loc(swing_idx)
            
            # Look back from swing high to find last bullish candle
            for i in range(swing_position - 1, max(0, swing_position - self.lookback), -1):
                candle_open = df['open'].iloc[i]
                candle_close = df['close'].iloc[i]
                
                # Is it a bullish (up) candle?
                if candle_close > candle_open:
                    ob = {
                        'type': 'bearish',
                        'index': i,
                        'timestamp': df.index[i],
                        'high': df['high'].iloc[i],
                        'low': df['low'].iloc[i],
                        'open': candle_open,
                        'close': candle_close,
                        'related_swing': swing_idx,
                        'tested': False
                    }
                    order_blocks.append(ob)
                    break  # Found it, move to next swing
        
        return order_blocks
    
    def detect_all_order_blocks(self, df):
        """
        Detect both bullish and bearish Order Blocks
        
        Args:
            df (pd.DataFrame): OHLCV data
            
        Returns:
            list: Combined list of all Order Blocks
        """
        bullish = self.detect_bullish_ob(df)
        bearish = self.detect_bearish_ob(df)
        
        all_obs = bullish + bearish
        all_obs.sort(key=lambda x: x['index'])
        
        return all_obs
    
    def mark_obs_on_dataframe(self, df):
        """
        Add Order Block markers to DataFrame
        
        Args:
            df (pd.DataFrame): OHLCV data
            
        Returns:
            pd.DataFrame: Data with OB columns added
        """
        df = df.copy()
        df['bullish_ob'] = False
        df['bearish_ob'] = False
        
        obs = self.detect_all_order_blocks(df)
        
        for ob in obs:
            if ob['type'] == 'bullish':
                df.loc[df.index[ob['index']], 'bullish_ob'] = True
            else:
                df.loc[df.index[ob['index']], 'bearish_ob'] = True
        
        return df, obs


# Convenience function
def detect_order_blocks(df, lookback=20):
    """
    Quick function to detect all Order Blocks
    
    Args:
        df (pd.DataFrame): OHLCV data (must have swing points)
        lookback (int): Candles to look back
        
    Returns:
        tuple: (DataFrame with markers, list of OBs)
    """
    detector = OrderBlockDetector(lookback)
    return detector.mark_obs_on_dataframe(df)
