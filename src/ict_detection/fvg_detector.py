"""
Fair Value Gap (FVG) Detection Module
Detects price imbalances (gaps) in the market
"""

import pandas as pd
import numpy as np


class FVGDetector:
    """Detects Fair Value Gaps in price data"""
    
    def __init__(self, min_gap_size=0):
        """
        Initialize FVG detector
        
        Args:
            min_gap_size (float): Minimum gap size to consider (0 = any gap)
        """
        self.min_gap_size = min_gap_size
    
    def detect_bullish_fvg(self, df):
        """
        Detect bullish Fair Value Gaps
        
        A bullish FVG occurs when:
        - Candle 1's high < Candle 3's low
        - This creates a "gap" that price jumped over
        - The gap is between candle 1 high and candle 3 low
        
        Args:
            df (pd.DataFrame): OHLCV data
            
        Returns:
            list: List of FVG dictionaries with details
        """
        fvgs = []
        
        for i in range(2, len(df)):
            candle_1_high = df['high'].iloc[i-2]
            candle_3_low = df['low'].iloc[i]
            
            # Check if there's a gap
            if candle_1_high < candle_3_low:
                gap_size = candle_3_low - candle_1_high
                
                # Only consider gaps above minimum size
                if gap_size >= self.min_gap_size:
                    fvg = {
                        'type': 'bullish',
                        'start_index': i-2,
                        'end_index': i,
                        'start_time': df.index[i-2],
                        'end_time': df.index[i],
                        'gap_low': candle_1_high,
                        'gap_high': candle_3_low,
                        'gap_size': gap_size,
                        'filled': False,
                        'fill_index': None
                    }
                    fvgs.append(fvg)
        
        return fvgs
    
    def detect_bearish_fvg(self, df):
        """
        Detect bearish Fair Value Gaps
        
        A bearish FVG occurs when:
        - Candle 1's low > Candle 3's high
        - This creates a "gap" that price jumped down through
        - The gap is between candle 3 high and candle 1 low
        
        Args:
            df (pd.DataFrame): OHLCV data
            
        Returns:
            list: List of FVG dictionaries with details
        """
        fvgs = []
        
        for i in range(2, len(df)):
            candle_1_low = df['low'].iloc[i-2]
            candle_3_high = df['high'].iloc[i]
            
            # Check if there's a gap
            if candle_1_low > candle_3_high:
                gap_size = candle_1_low - candle_3_high
                
                if gap_size >= self.min_gap_size:
                    fvg = {
                        'type': 'bearish',
                        'start_index': i-2,
                        'end_index': i,
                        'start_time': df.index[i-2],
                        'end_time': df.index[i],
                        'gap_low': candle_3_high,
                        'gap_high': candle_1_low,
                        'gap_size': gap_size,
                        'filled': False,
                        'fill_index': None
                    }
                    fvgs.append(fvg)
        
        return fvgs
    
    def detect_all_fvgs(self, df):
        """
        Detect both bullish and bearish FVGs
        
        Args:
            df (pd.DataFrame): OHLCV data
            
        Returns:
            list: Combined list of all FVGs, sorted by time
        """
        bullish = self.detect_bullish_fvg(df)
        bearish = self.detect_bearish_fvg(df)
        
        all_fvgs = bullish + bearish
        all_fvgs.sort(key=lambda x: x['start_index'])
        
        return all_fvgs
    
    def check_fvg_filled(self, df, fvg):
        """
        Check if an FVG has been filled (price returned to gap)
        
        Args:
            df (pd.DataFrame): OHLCV data
            fvg (dict): FVG to check
            
        Returns:
            bool: True if filled, False otherwise
        """
        # Look at candles after the FVG formation
        for i in range(fvg['end_index'] + 1, len(df)):
            candle_high = df['high'].iloc[i]
            candle_low = df['low'].iloc[i]
            
            # Check if price entered the gap
            if fvg['type'] == 'bullish':
                # Bullish FVG filled if price drops back into gap
                if candle_low <= fvg['gap_high']:
                    fvg['filled'] = True
                    fvg['fill_index'] = i
                    fvg['fill_time'] = df.index[i]
                    return True
            else:  # bearish
                # Bearish FVG filled if price rises back into gap
                if candle_high >= fvg['gap_low']:
                    fvg['filled'] = True
                    fvg['fill_index'] = i
                    fvg['fill_time'] = df.index[i]
                    return True
        
        return False
    
    def mark_fvgs_on_dataframe(self, df):
        """
        Add FVG markers to DataFrame
        
        Args:
            df (pd.DataFrame): OHLCV data
            
        Returns:
            pd.DataFrame: Data with FVG columns added
        """
        df = df.copy()
        df['bullish_fvg'] = False
        df['bearish_fvg'] = False
        
        fvgs = self.detect_all_fvgs(df)
        
        for fvg in fvgs:
            if fvg['type'] == 'bullish':
                df.loc[df.index[fvg['end_index']], 'bullish_fvg'] = True
            else:
                df.loc[df.index[fvg['end_index']], 'bearish_fvg'] = True
        
        return df, fvgs


# Convenience function
def detect_fvgs(df, min_gap_size=0):
    """
    Quick function to detect all FVGs
    
    Args:
        df (pd.DataFrame): OHLCV data
        min_gap_size (float): Minimum gap size
        
    Returns:
        tuple: (DataFrame with markers, list of FVGs)
    """
    detector = FVGDetector(min_gap_size)
    return detector.mark_fvgs_on_dataframe(df)
