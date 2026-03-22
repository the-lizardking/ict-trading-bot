"""
Data Loader Module
Handles loading OHLCV data from CSV files and preparing it for analysis
"""

import pandas as pd
from datetime import datetime
from pathlib import Path


class DataLoader:
    """Loads and validates cryptocurrency price data from CSV files"""
    
    def __init__(self, data_dir='data'):
        """
        Initialize the data loader
        
        Args:
            data_dir (str): Directory where data files are stored
        """
        self.data_dir = Path(data_dir)
        
    def load_csv(self, filename, date_column='timestamp'):
        """
        Load OHLCV data from a CSV file
        
        Args:
            filename (str): Name of the CSV file
            date_column (str): Name of the column containing dates/timestamps
            
        Returns:
            pd.DataFrame: DataFrame with OHLCV data and datetime index
        """
        filepath = self.data_dir / filename
        
        if not filepath.exists():
            raise FileNotFoundError(f"Data file not found: {filepath}")
        
        # Read CSV
        df = pd.read_csv(filepath)
        
        # Convert timestamp to datetime
        if date_column in df.columns:
            df[date_column] = pd.to_datetime(df[date_column])
            df.set_index(date_column, inplace=True)
        
        # Ensure required columns exist
        required_cols = ['open', 'high', 'low', 'close', 'volume']
        missing_cols = [col for col in required_cols if col not in df.columns]
        
        if missing_cols:
            raise ValueError(f"Missing required columns: {missing_cols}")
        
        # Sort by timestamp
        df.sort_index(inplace=True)
        
        print(f"✓ Loaded {len(df)} candles from {filename}")
        print(f"  Date range: {df.index[0]} to {df.index[-1]}")
        
        return df
    
    def validate_data(self, df):
        """
        Check data for common issues
        
        Args:
            df (pd.DataFrame): OHLCV DataFrame to validate
            
        Returns:
            dict: Validation results
        """
        issues = []
        
        # Check for missing values
        missing = df.isnull().sum()
        if missing.any():
            issues.append(f"Missing values found: {missing[missing > 0].to_dict()}")
        
        # Check for negative prices
        price_cols = ['open', 'high', 'low', 'close']
        for col in price_cols:
            if (df[col] <= 0).any():
                issues.append(f"Negative or zero values in {col}")
        
        # Check high/low logic
        if ((df['high'] < df['low']).any()):
            issues.append("Some candles have high < low")
        
        # Check if high is actually the highest
        if ((df['high'] < df['open']).any() or (df['high'] < df['close']).any()):
            issues.append("High is not the highest price in some candles")
        
        # Check if low is actually the lowest
        if ((df['low'] > df['open']).any() or (df['low'] > df['close']).any()):
            issues.append("Low is not the lowest price in some candles")
        
        return {
            'is_valid': len(issues) == 0,
            'issues': issues,
            'num_candles': len(df),
            'date_range': (str(df.index[0]), str(df.index[-1]))
        }


# Convenience function for quick loading
def load_data(filename, data_dir='data'):
    """
    Quick function to load data
    
    Args:
        filename (str): CSV filename
        data_dir (str): Data directory
        
    Returns:
        pd.DataFrame: Loaded OHLCV data
    """
    loader = DataLoader(data_dir)
    return loader.load_csv(filename)
