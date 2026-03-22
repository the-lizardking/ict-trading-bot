"""
Data Download Script
Downloads historical Bitcoin data from Binance API (free, no account needed)
"""

import requests
import pandas as pd
from datetime import datetime, timedelta
import time

def download_binance_data(symbol='BTCUSDT', interval='1m', days=7):
    """
    Download OHLCV data from Binance
    
    Args:
        symbol (str): Trading pair (e.g., 'BTCUSDT')
        interval (str): Candle interval (1m, 5m, 15m, 1h, etc.)
        days (int): Number of days of historical data to download
        
    Returns:
        pd.DataFrame: OHLCV data
    """
    print(f"\n📊 Downloading {symbol} {interval} data for last {days} days...")
    print(f"   (Free data from Binance API - no account needed)\n")
    
    # Binance API endpoint
    base_url = 'https://api.binance.com/api/v3/klines'
    
    # Calculate time range
    end_time = int(datetime.now().timestamp() * 1000)
    start_time = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    
    all_data = []
    current_time = start_time
    
    # Binance limits to 1000 candles per request
    limit = 1000
    
    while current_time < end_time:
        params = {
            'symbol': symbol,
            'interval': interval,
            'startTime': current_time,
            'endTime': end_time,
            'limit': limit
        }
        
        try:
            response = requests.get(base_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if not data:
                break
            
            all_data.extend(data)
            current_time = data[-1][0] + 1
            
            print(f"   Downloaded {len(all_data)} candles...", end='\r')
            
            # Be nice to the API
            time.sleep(0.5)
            
        except Exception as e:
            print(f"\n⚠ Error downloading data: {e}")
            break
    
    if not all_data:
        print("\n✗ No data downloaded")
        return None
    
    # Convert to DataFrame
    df = pd.DataFrame(all_data, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
        'taker_buy_quote', 'ignore'
    ])
    
    # Keep only needed columns
    df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
    
    # Convert types
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    
    print(f"\n✓ Downloaded {len(df)} candles successfully!")
    print(f"  Date range: {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")
    
    return df

def save_data(df, filename='btc_1m_sample.csv'):
    """Save DataFrame to CSV in data folder"""
    import os
    
    # Create data folder if it doesn't exist
    os.makedirs('data', exist_ok=True)
    
    filepath = f'data/{filename}'
    df.to_csv(filepath, index=False)
    print(f"\n💾 Saved to: {filepath}")
    print(f"   File size: {os.path.getsize(filepath) / 1024:.1f} KB")

if __name__ == "__main__":
    print("\n" + "="*60)
    print("BITCOIN DATA DOWNLOADER".center(60))
    print("="*60)
    
    # Download 7 days of 1-minute data (about 10,000 candles)
    df = download_binance_data(symbol='BTCUSDT', interval='1m', days=7)
    
    if df is not None:
        # Save to CSV
        save_data(df, 'btc_1m_sample.csv')
        
        print("\n" + "="*60)
        print("✓ DATA READY TO USE!")
        print("="*60)
        print("\nNext step: Run the test with:")
        print("  python3 tests/test_data_loader.py")
        print("\n")
    else:
        print("\n✗ Download failed. Try again or use Option 2 below.\n")
