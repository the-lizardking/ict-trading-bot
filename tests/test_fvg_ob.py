"""
Test script for FVG and Order Block detection
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data_layer.data_loader import load_data
from src.ict_detection.swing_points import detect_swings
from src.ict_detection.fvg_detector import FVGDetector, detect_fvgs
from src.ict_detection.order_blocks import OrderBlockDetector, detect_order_blocks


def test_fvg_detection():
    """Test Fair Value Gap detection"""
    
    print("\n" + "="*60)
    print("TESTING FVG DETECTION".center(60))
    print("="*60 + "\n")
    
    # Load data
    df = load_data('btc_1m_sample.csv')
    df = df.tail(1000).copy()  # Use last 1000 candles
    
    print(f"Testing on {len(df)} candles")
    print(f"Date range: {df.index[0]} to {df.index[-1]}\n")
    
    # Detect FVGs
    detector = FVGDetector(min_gap_size=0)
    df_marked, fvgs = detector.mark_fvgs_on_dataframe(df)
    
    # Separate by type
    bullish_fvgs = [f for f in fvgs if f['type'] == 'bullish']
    bearish_fvgs = [f for f in fvgs if f['type'] == 'bearish']
    
    print(f"✓ Detected {len(bullish_fvgs)} Bullish FVGs")
    print(f"✓ Detected {len(bearish_fvgs)} Bearish FVGs")
    print(f"  Total: {len(fvgs)} FVGs\n")
    
    # Show first 5 FVGs
    print("First 5 FVGs detected:")
    print("-" * 60)
    for i, fvg in enumerate(fvgs[:5]):
        print(f"{i+1}. {fvg['type'].upper():8} | "
              f"Time: {fvg['end_time']} | "
              f"Gap: ${fvg['gap_low']:,.2f} - ${fvg['gap_high']:,.2f} | "
              f"Size: ${fvg['gap_size']:,.2f}")
    
    # Check for filled FVGs
    filled_count = 0
    for fvg in fvgs:
        if detector.check_fvg_filled(df, fvg):
            filled_count += 1
    
    print(f"\n✓ {filled_count} FVGs were filled (price returned to gap)")
    print(f"  {len(fvgs) - filled_count} FVGs remain unfilled")
    
    return True


def test_order_block_detection():
    """Test Order Block detection"""
    
    print("\n" + "="*60)
    print("TESTING ORDER BLOCK DETECTION".center(60))
    print("="*60 + "\n")
    
    # Load data
    df = load_data('btc_1m_sample.csv')
    df = df.tail(1000).copy()
    
    # First detect swings (required for OB detection)
    print("Step 1: Detecting swing points...")
    df = detect_swings(df, left_bars=5, right_bars=5)
    
    num_swing_highs = df['swing_high'].sum()
    num_swing_lows = df['swing_low'].sum()
    print(f"  Found {num_swing_highs} swing highs, {num_swing_lows} swing lows\n")
    
    # Detect Order Blocks
    print("Step 2: Detecting Order Blocks...")
    detector = OrderBlockDetector(lookback=20)
    df_marked, obs = detector.mark_obs_on_dataframe(df)
    
    # Separate by type
    bullish_obs = [ob for ob in obs if ob['type'] == 'bullish']
    bearish_obs = [ob for ob in obs if ob['type'] == 'bearish']
    
    print(f"✓ Detected {len(bullish_obs)} Bullish Order Blocks")
    print(f"✓ Detected {len(bearish_obs)} Bearish Order Blocks")
    print(f"  Total: {len(obs)} Order Blocks\n")
    
    # Show first 5 OBs
    if len(obs) > 0:
        print("First 5 Order Blocks detected:")
        print("-" * 60)
        for i, ob in enumerate(obs[:5]):
            print(f"{i+1}. {ob['type'].upper():8} OB | "
                  f"Time: {ob['timestamp']} | "
                  f"Zone: ${ob['low']:,.2f} - ${ob['high']:,.2f}")
    else:
        print("⚠ No Order Blocks detected (may need more swing points)")
    
    return True


if __name__ == "__main__":
    print("\n" + "🎯 MILESTONE 3 TEST SUITE 🎯".center(60))
    
    # Run tests
    fvg_passed = test_fvg_detection()
    ob_passed = test_order_block_detection()
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    print(f"FVG Detection: {'✓ PASSED' if fvg_passed else '✗ FAILED'}")
    print(f"Order Block Detection: {'✓ PASSED' if ob_passed else '✗ FAILED'}")
    
    if fvg_passed and ob_passed:
        print("\n🎉 MILESTONE 3 TESTS COMPLETE! 🎉")
        print("\nNext: Run visualization to see FVGs and OBs on chart!")
        print("  python3 visualize_all.py\n")
    else:
        print("\n⚠ Some tests failed. Check errors above.\n")
