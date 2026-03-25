"""
Liquidity Detection Module
Identifies liquidity pools, sweeps, and equal highs/lows
"""

import pandas as pd
import numpy as np


class LiquidityDetector:
    """Detects liquidity pools and sweeps"""
    
    def __init__(self, tolerance=0.001):
        """
        Initialize liquidity detector
        
        Args:
            tolerance (float): Price similarity threshold (0.001 = 0.1%)
        """
        self.tolerance = tolerance
    
    def detect_equal_highs(self, df, min_touches=2, lookback=50):
        """
        Detect equal highs (Buy Side Liquidity - BSL)
        
        Multiple swing highs at similar price = stops clustered above
        
        Args:
            df (pd.DataFrame): OHLCV data with swing_high column
            min_touches (int): Minimum number of equal highs needed
            lookback (int): How far back to look for equal highs
            
        Returns:
            list: List of liquidity pool dictionaries
        """
        if 'swing_high' not in df.columns:
            print("⚠ Warning: swing_high column not found")
            return []
        
        liquidity_pools = []
        swing_highs = df[df['swing_high']]
        
        for i, (idx, row) in enumerate(swing_highs.iterrows()):
            current_price = row['high']
            equal_count = 1
            equal_indices = [df.index.get_loc(idx)]
            
            # Look back for similar highs
            for j in range(max(0, i - lookback), i):
                compare_idx = swing_highs.iloc[j].name
                compare_price = df.loc[compare_idx, 'high']
                
                # Check if prices are equal (within tolerance)
                if abs(current_price - compare_price) / current_price <= self.tolerance:
                    equal_count += 1
                    equal_indices.append(df.index.get_loc(compare_idx))
            
            # If enough equal highs found, mark as liquidity pool
            if equal_count >= min_touches:
                pool = {
                    'type': 'buy_side',  # BSL - stops above
                    'price': current_price,
                    'touches': equal_count,
                    'first_touch': df.index[min(equal_indices)],
                    'last_touch': idx,
                    'swept': False,
                    'sweep_time': None
                }
                liquidity_pools.append(pool)
        
        return liquidity_pools
    
    def detect_equal_lows(self, df, min_touches=2, lookback=50):
        """
        Detect equal lows (Sell Side Liquidity - SSL)
        
        Multiple swing lows at similar price = stops clustered below
        
        Args:
            df (pd.DataFrame): OHLCV data with swing_low column
            min_touches (int): Minimum number of equal lows needed
            lookback (int): How far back to look
            
        Returns:
            list: List of liquidity pool dictionaries
        """
        if 'swing_low' not in df.columns:
            print("⚠ Warning: swing_low column not found")
            return []
        
        liquidity_pools = []
        swing_lows = df[df['swing_low']]
        
        for i, (idx, row) in enumerate(swing_lows.iterrows()):
            current_price = row['low']
            equal_count = 1
            equal_indices = [df.index.get_loc(idx)]
            
            # Look back for similar lows
            for j in range(max(0, i - lookback), i):
                compare_idx = swing_lows.iloc[j].name
                compare_price = df.loc[compare_idx, 'low']
                
                if abs(current_price - compare_price) / current_price <= self.tolerance:
                    equal_count += 1
                    equal_indices.append(df.index.get_loc(compare_idx))
            
            if equal_count >= min_touches:
                pool = {
                    'type': 'sell_side',  # SSL - stops below
                    'price': current_price,
                    'touches': equal_count,
                    'first_touch': df.index[min(equal_indices)],
                    'last_touch': idx,
                    'swept': False,
                    'sweep_time': None
                }
                liquidity_pools.append(pool)
        
        return liquidity_pools
    
    def detect_liquidity_sweeps(self, df, liquidity_pools):
        """
        Detect when liquidity pools are swept
        
        A sweep occurs when:
        - Price pierces through the liquidity level
        - Then quickly reverses (takes the liquidity and runs)
        
        Args:
            df (pd.DataFrame): OHLCV data
            liquidity_pools (list): Detected liquidity pools
            
        Returns:
            list: Updated liquidity pools with sweep info
        """
        for pool in liquidity_pools:
            last_touch_idx = df.index.get_loc(pool['last_touch'])
            
            # Look forward from last touch
            for i in range(last_touch_idx + 1, len(df)):
                candle_high = df['high'].iloc[i]
                candle_low = df['low'].iloc[i]
                
                if pool['type'] == 'buy_side':
                    # BSL swept when price goes above and reverses down
                    if candle_high > pool['price']:
                        pool['swept'] = True
                        pool['sweep_time'] = df.index[i]
                        pool['sweep_index'] = i
                        break
                        
                else:  # sell_side
                    # SSL swept when price goes below and reverses up
                    if candle_low < pool['price']:
                        pool['swept'] = True
                        pool['sweep_time'] = df.index[i]
                        pool['sweep_index'] = i
                        break
        
        return liquidity_pools
    
    def detect_all_liquidity(self, df, min_touches=2, lookback=50):
        """
        Detect all liquidity pools and sweeps
        
        Args:
            df (pd.DataFrame): OHLCV data with swing points
            min_touches (int): Minimum equal highs/lows
            lookback (int): Lookback period
            
        Returns:
            list: All liquidity pools
        """
        equal_highs = self.detect_equal_highs(df, min_touches, lookback)
        equal_lows = self.detect_equal_lows(df, min_touches, lookback)
        
        all_pools = equal_highs + equal_lows
        all_pools = self.detect_liquidity_sweeps(df, all_pools)
        
        return all_pools


# Convenience function
def detect_liquidity(df, tolerance=0.001, min_touches=2, lookback=50):
    """
    Quick function to detect liquidity
    
    Args:
        df (pd.DataFrame): OHLCV data
        tolerance (float): Price similarity threshold
        min_touches (int): Min equal highs/lows
        lookback (int): Lookback period
        
    Returns:
        list: Liquidity pools
    """
    detector = LiquidityDetector(tolerance)
    return detector.detect_all_liquidity(df, min_touches, lookback)
