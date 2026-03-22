"""
Test script for swing point detection
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data_layer.data_loader import load_data
from src.ict_detection.swing_points import SwingPointDetector, detect_swings

def test_swing_detection():
    """Test swing point detection on Bitcoin data"""
    
    print("\n" + "="*60)
    print("TESTING SWING POINT DETECTION".center(60))
    print("="*60 + "\n")
    
    # Load data
    print("Loading Bitcoin data...")
    df = load_data('btc_1m_sample.csv')
    
    # Use last 500 candles for testing (easier to visualize)
    df = df.tail(500).copy()
    print(f"Using last 500 candles for testing")
    print(f"Date range: {df.index[0]} to {df.index[-1]}\n")
    
    # Detect swings
    print("Detecting swing points...")
    detector = SwingPointDetector(left_bars=5, right_bars=5)
    
    df['swing_high'] = detector.detect_swing_highs(df)
    df['swing_low'] = detector.detect_swing_lows(df)
    
    # Count swings
    num_highs = df['swing_high'].sum()
    num_lows = df['swing_low'].sum()
    
    print(f"✓ Detected {num_highs} swing highs")
    print(f"✓ Detected {num_lows} swing lows")
    print(f"  Total swing points: {num_highs + num_lows}")
    
    # Get swing point list
    swing_points = detector.get_swing_points_list(df)
    
    print(f"\nFirst 10 swing points:")
    print("-" * 60)
    for i, sp in enumerate(swing_points[:10]):
        print(f"{i+1}. {sp['type'].upper():5} at {sp['timestamp']} | Price: ${sp['price']:,.2f}")
    
    # Mark market structure
    print("\nDetecting market structure (BOS/CHoCH)...")
    df = detector.mark_market_structure(df)
    
    num_bos = df['bos'].notna().sum()
    num_choch = df['choch'].notna().sum()
    
    print(f"✓ Detected {num_bos} Break of Structure events")
    print(f"✓ Detected {num_choch} Change of Character events")
    
    # Show some BOS/CHoCH events
    bos_events = df[df['bos'].notna()][['bos', 'close']].head(5)
    if len(bos_events) > 0:
        print(f"\nFirst {len(bos_events)} BOS events:")
        print(bos_events)
    
    choch_events = df[df['choch'].notna()][['choch', 'close']].head(5)
    if len(choch_events) > 0:
        print(f"\nFirst {len(choch_events)} CHoCH events:")
        print(choch_events)
    
    print("\n" + "="*60)
    print("✓ SWING DETECTION TEST COMPLETE!".center(60))
    print("="*60)
    print("\nNext: Run visualization to see swing points on a chart!")
    print("  python3 visualize_swings.py\n")
    
    return True

if __name__ == "__main__":
    test_swing_detection()
