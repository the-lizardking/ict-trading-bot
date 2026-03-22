"""
Swing Point Detection Module
Identifies swing highs, swing lows, and market structure (BOS/CHoCH)
"""

import pandas as pd
import numpy as np


class SwingPointDetector:
    """Detects swing points and market structure in price data"""
    
    def __init__(self, left_bars=5, right_bars=5):
        """
        Initialize detector
        
        Args:
            left_bars (int): Number of bars to the left that must be lower/higher
            right_bars (int): Number of bars to the right that must be lower/higher
        """
        self.left_bars = left_bars
        self.right_bars = right_bars
    
    def detect_swing_highs(self, df):
        """
        Detect swing highs in the data
        
        A swing high is a bar where:
        - Its high is higher than the highs of X bars to the left
        - Its high is higher than the highs of X bars to the right
        
        Args:
            df (pd.DataFrame): OHLCV data
            
        Returns:
            pd.Series: Boolean series marking swing highs
        """
        highs = df['high'].values
        swing_highs = np.zeros(len(df), dtype=bool)
        
        # Can't have swing at the edges
        for i in range(self.left_bars, len(df) - self.right_bars):
            is_swing = True
            
            # Check left bars
            for j in range(1, self.left_bars + 1):
                if highs[i] <= highs[i - j]:
                    is_swing = False
                    break
            
            if not is_swing:
                continue
            
            # Check right bars
            for j in range(1, self.right_bars + 1):
                if highs[i] <= highs[i + j]:
                    is_swing = False
                    break
            
            swing_highs[i] = is_swing
        
        return pd.Series(swing_highs, index=df.index)
    
    def detect_swing_lows(self, df):
        """
        Detect swing lows in the data
        
        A swing low is a bar where:
        - Its low is lower than the lows of X bars to the left
        - Its low is lower than the lows of X bars to the right
        
        Args:
            df (pd.DataFrame): OHLCV data
            
        Returns:
            pd.Series: Boolean series marking swing lows
        """
        lows = df['low'].values
        swing_lows = np.zeros(len(df), dtype=bool)
        
        for i in range(self.left_bars, len(df) - self.right_bars):
            is_swing = True
            
            # Check left bars
            for j in range(1, self.left_bars + 1):
                if lows[i] >= lows[i - j]:
                    is_swing = False
                    break
            
            if not is_swing:
                continue
            
            # Check right bars
            for j in range(1, self.right_bars + 1):
                if lows[i] >= lows[i + j]:
                    is_swing = False
                    break
            
            swing_lows[i] = is_swing
        
        return pd.Series(swing_lows, index=df.index)
    
    def mark_market_structure(self, df):
        """
        Identify market structure: BOS (Break of Structure) and CHoCH (Change of Character)
        
        BOS: Price breaks above the most recent swing high (bullish) or below swing low (bearish)
        CHoCH: Market structure changes from bullish to bearish or vice versa
        
        Args:
            df (pd.DataFrame): OHLCV data with swing points marked
            
        Returns:
            pd.DataFrame: Original df with BOS and CHoCH columns added
        """
        df = df.copy()
        
        # Detect swings if not already done
        if 'swing_high' not in df.columns:
            df['swing_high'] = self.detect_swing_highs(df)
        if 'swing_low' not in df.columns:
            df['swing_low'] = self.detect_swing_lows(df)
        
        # Initialize columns
        df['bos'] = None  # 'bullish' or 'bearish'
        df['choch'] = None  # 'bullish_to_bearish' or 'bearish_to_bullish'
        
        # Track last swing high/low
        last_swing_high = None
        last_swing_high_price = None
        last_swing_low = None
        last_swing_low_price = None
        
        # Track current structure
        current_structure = None  # 'bullish' or 'bearish'
        
        for i in range(len(df)):
            # Update swing highs
            if df['swing_high'].iloc[i]:
                last_swing_high = i
                last_swing_high_price = df['high'].iloc[i]
            
            # Update swing lows
            if df['swing_low'].iloc[i]:
                last_swing_low = i
                last_swing_low_price = df['low'].iloc[i]
            
            # Check for BOS and CHoCH
            if last_swing_high_price is not None and last_swing_low_price is not None:
                current_high = df['high'].iloc[i]
                current_low = df['low'].iloc[i]
                
                # Bullish BOS: price breaks above last swing high
                if current_high > last_swing_high_price:
                    if current_structure == 'bearish':
                        # Structure was bearish, now breaking up = CHoCH
                        df.loc[df.index[i], 'choch'] = 'bearish_to_bullish'
                    else:
                        # Continuing or establishing bullish structure = BOS
                        df.loc[df.index[i], 'bos'] = 'bullish'
                    current_structure = 'bullish'
                
                # Bearish BOS: price breaks below last swing low
                elif current_low < last_swing_low_price:
                    if current_structure == 'bullish':
                        # Structure was bullish, now breaking down = CHoCH
                        df.loc[df.index[i], 'choch'] = 'bullish_to_bearish'
                    else:
                        # Continuing or establishing bearish structure = BOS
                        df.loc[df.index[i], 'bos'] = 'bearish'
                    current_structure = 'bearish'
        
        return df
    
    def get_swing_points_list(self, df):
        """
        Get list of all swing points with their details
        
        Args:
            df (pd.DataFrame): OHLCV data
            
        Returns:
            list: List of dictionaries containing swing point info
        """
        df = df.copy()
        df['swing_high'] = self.detect_swing_highs(df)
        df['swing_low'] = self.detect_swing_lows(df)
        
        swing_points = []
        
        # Swing highs
        for idx in df[df['swing_high']].index:
            swing_points.append({
                'timestamp': idx,
                'type': 'high',
                'price': df.loc[idx, 'high'],
                'index': df.index.get_loc(idx)
            })
        
        # Swing lows
        for idx in df[df['swing_low']].index:
            swing_points.append({
                'timestamp': idx,
                'type': 'low',
                'price': df.loc[idx, 'low'],
                'index': df.index.get_loc(idx)
            })
        
        # Sort by timestamp
        swing_points.sort(key=lambda x: x['timestamp'])
        
        return swing_points


# Convenience function
def detect_swings(df, left_bars=5, right_bars=5):
    """
    Quick function to detect swings and mark structure
    
    Args:
        df (pd.DataFrame): OHLCV data
        left_bars (int): Bars to left for swing confirmation
        right_bars (int): Bars to right for swing confirmation
        
    Returns:
        pd.DataFrame: Data with swing points and structure marked
    """
    detector = SwingPointDetector(left_bars, right_bars)
    df = df.copy()
    df['swing_high'] = detector.detect_swing_highs(df)
    df['swing_low'] = detector.detect_swing_lows(df)
    df = detector.mark_market_structure(df)
    return df
