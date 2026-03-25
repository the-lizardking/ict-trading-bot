"""
Key Levels Detection Module
Identifies important price reference points (daily/weekly highs, session opens)
"""

import pandas as pd
import numpy as np
from datetime import time


class KeyLevelsDetector:
    """Detects key price levels"""
    
    def __init__(self):
        """Initialize key levels detector"""
        pass
    
    def calculate_daily_levels(self, df):
        """
        Calculate Previous Day High (PDH) and Low (PDL)
        
        Args:
            df (pd.DataFrame): OHLCV data with datetime index
            
        Returns:
            pd.DataFrame: Data with PDH/PDL columns
        """
        df = df.copy()
        
        # Group by date
        df['date'] = df.index.date
        
        # Calculate daily high and low
        daily_high = df.groupby('date')['high'].max()
        daily_low = df.groupby('date')['low'].min()
        
        # Shift to get previous day
        df['pdh'] = df['date'].map(daily_high.shift(1))
        df['pdl'] = df['date'].map(daily_low.shift(1))
        
        df.drop('date', axis=1, inplace=True)
        
        return df
    
    def calculate_weekly_levels(self, df):
        """
        Calculate Previous Week High (PWH) and Low (PWL)
        
        Args:
            df (pd.DataFrame): OHLCV data
            
        Returns:
            pd.DataFrame: Data with PWH/PWL columns
        """
        df = df.copy()
        
        # Group by week
        df['week'] = df.index.isocalendar().week
        df['year'] = df.index.year
        df['year_week'] = df['year'].astype(str) + '_' + df['week'].astype(str)
        
        # Calculate weekly high and low
        weekly_high = df.groupby('year_week')['high'].max()
        weekly_low = df.groupby('year_week')['low'].min()
        
        # Shift to get previous week
        df['pwh'] = df['year_week'].map(weekly_high.shift(1))
        df['pwl'] = df['year_week'].map(weekly_low.shift(1))
        
        df.drop(['week', 'year', 'year_week'], axis=1, inplace=True)
        
        return df
    
    def identify_session_opens(self, df):
        """
        Identify session open times and prices
        
        Trading sessions:
        - Asian: 00:00 UTC
        - London: 08:00 UTC  
        - New York: 13:00 UTC
        
        Args:
            df (pd.DataFrame): OHLCV data
            
        Returns:
            pd.DataFrame: Data with session open columns
        """
        df = df.copy()
        
        # Add hour column
        df['hour'] = df.index.hour
        
        # Mark session opens
        df['asian_open'] = (df['hour'] == 0)
        df['london_open'] = (df['hour'] == 8)
        df['ny_open'] = (df['hour'] == 13)
        
        # Get session open prices
        df['asian_open_price'] = np.where(df['asian_open'], df['open'], np.nan)
        df['london_open_price'] = np.where(df['london_open'], df['open'], np.nan)
        df['ny_open_price'] = np.where(df['ny_open'], df['open'], np.nan)
        
        # Forward fill to make them available throughout the session
        df['asian_open_price'].fillna(method='ffill', inplace=True)
        df['london_open_price'].fillna(method='ffill', inplace=True)
        df['ny_open_price'].fillna(method='ffill', inplace=True)
        
        df.drop('hour', axis=1, inplace=True)
        
        return df
    
    def get_all_key_levels(self, df):
        """
        Calculate all key levels
        
        Args:
            df (pd.DataFrame): OHLCV data
            
        Returns:
            pd.DataFrame: Data with all key level columns
        """
        df = self.calculate_daily_levels(df)
        df = self.calculate_weekly_levels(df)
        df = self.identify_session_opens(df)
        
        return df


# Convenience function
def detect_key_levels(df):
    """
    Quick function to detect key levels
    
    Args:
        df (pd.DataFrame): OHLCV data
        
    Returns:
        pd.DataFrame: Data with key levels marked
    """
    detector = KeyLevelsDetector()
    return detector.get_all_key_levels(df)
