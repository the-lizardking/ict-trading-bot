"""
Test script for data loader
Run this to verify your data loading works
"""

import sys
from pathlib import Path

# Add parent directory to path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data_layer.data_loader import DataLoader, load_data
from src.data_layer.database import Database

def test_data_loading():
    """Test loading Bitcoin data"""
    print("\n" + "="*50)
    print("Testing Data Loader")
    print("="*50 + "\n")
    
    # Load data
    try:
        df = load_data('btc_1m_sample.csv')
        print(f"\n✓ Successfully loaded data!")
        print(f"  Rows: {len(df)}")
        print(f"  Columns: {list(df.columns)}")
        print(f"\nFirst few rows:")
        print(df.head())
        
        # Validate data
        loader = DataLoader()
        validation = loader.validate_data(df)
        
        print(f"\n{'='*50}")
        print("Data Validation Results")
        print("="*50)
        print(f"Valid: {validation['is_valid']}")
        print(f"Number of candles: {validation['num_candles']}")
        print(f"Date range: {validation['date_range'][0]} to {validation['date_range'][1]}")
        
        if validation['issues']:
            print("\nIssues found:")
            for issue in validation['issues']:
                print(f"  ⚠ {issue}")
        else:
            print("\n✓ No issues found - data is clean!")
            
    except Exception as e:
        print(f"\n✗ Error: {e}")
        return False
    
    return True

def test_database():
    """Test database creation"""
    print("\n" + "="*50)
    print("Testing Database")
    print("="*50 + "\n")
    
    try:
        db = Database()
        print("✓ Database created successfully!")
        print(f"  Location: {db.db_path}")
        
        # Test inserting a sample trade
        sample_trade = {
            'timestamp': '2026-03-22 11:00:00',
            'symbol': 'BTCUSDT',
            'direction': 'long',
            'entry_price': 65000.0,
            'position_size': 0.01,
            'setup_type': 'FVG',
            'status': 'open',
            'is_backtest': 1
        }
        
        trade_id = db.insert_trade(sample_trade)
        print(f"✓ Sample trade inserted with ID: {trade_id}")
        
        # Retrieve the trade
        trades = db.get_trades(limit=1)
        print(f"✓ Retrieved {len(trades)} trade(s) from database")
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    print("\n" + "🚀 MILESTONE 1 TEST SUITE 🚀".center(50))
    
    # Run tests
    data_test_passed = test_data_loading()
    db_test_passed = test_database()
    
    # Summary
    print("\n" + "="*50)
    print("TEST SUMMARY")
    print("="*50)
    print(f"Data Loader: {'✓ PASSED' if data_test_passed else '✗ FAILED'}")
    print(f"Database: {'✓ PASSED' if db_test_passed else '✗ FAILED'}")
    
    if data_test_passed and db_test_passed:
        print("\n🎉 MILESTONE 1 COMPLETE! 🎉")
        print("\nYou can now:")
        print("  • Load Bitcoin price data from CSV files")
        print("  • Store trades in a database")
        print("  • Move on to Milestone 2 (ICT pattern detection)")
    else:
        print("\n⚠ Some tests failed. Check errors above.")
    
    print("\n")
