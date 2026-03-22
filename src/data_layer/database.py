"""
Database Module
Handles SQLite database operations for storing trades, backtests, and strategy versions
"""

import sqlite3
from pathlib import Path
from datetime import datetime
import json


class Database:
    """Manages SQLite database for trade journal and backtest results"""
    
    def __init__(self, db_path='trade_journal.db'):
        """
        Initialize database connection
        
        Args:
            db_path (str): Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.conn = None
        self.create_tables()
    
    def connect(self):
        """Create database connection"""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row  # Allow dict-like access
        return self.conn
    
    def create_tables(self):
        """Create all necessary tables if they don't exist"""
        conn = self.connect()
        cursor = conn.cursor()
        
        # Trades table - stores all executed trades (backtest or live)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                stop_loss REAL,
                take_profit_1 REAL,
                take_profit_2 REAL,
                take_profit_3 REAL,
                position_size REAL NOT NULL,
                setup_type TEXT,
                killzone TEXT,
                bias TEXT,
                entry_reason TEXT,
                exit_reason TEXT,
                pnl REAL,
                pnl_percent REAL,
                status TEXT DEFAULT 'open',
                notes TEXT,
                is_backtest BOOLEAN DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Backtest results table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL,
                strategy_version TEXT,
                start_date TEXT,
                end_date TEXT,
                total_trades INTEGER,
                winning_trades INTEGER,
                losing_trades INTEGER,
                win_rate REAL,
                profit_factor REAL,
                expectancy REAL,
                max_drawdown REAL,
                max_drawdown_pct REAL,
                sharpe_ratio REAL,
                total_pnl REAL,
                total_pnl_pct REAL,
                avg_win REAL,
                avg_loss REAL,
                largest_win REAL,
                largest_loss REAL,
                config JSON,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Strategy versions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategy_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version_name TEXT UNIQUE NOT NULL,
                description TEXT,
                config JSON NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 0
            )
        ''')
        
        conn.commit()
        conn.close()
        
        print("✓ Database tables created/verified")
    
    def insert_trade(self, trade_data):
        """
        Insert a new trade record
        
        Args:
            trade_data (dict): Trade information
            
        Returns:
            int: ID of inserted trade
        """
        conn = self.connect()
        cursor = conn.cursor()
        
        columns = ', '.join(trade_data.keys())
        placeholders = ', '.join(['?' for _ in trade_data])
        query = f"INSERT INTO trades ({columns}) VALUES ({placeholders})"
        
        cursor.execute(query, list(trade_data.values()))
        trade_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return trade_id
    
    def get_trades(self, filters=None, limit=None):
        """
        Retrieve trades from database
        
        Args:
            filters (dict): Optional filters (e.g., {'symbol': 'BTCUSDT'})
            limit (int): Maximum number of trades to return
            
        Returns:
            list: List of trade records as dictionaries
        """
        conn = self.connect()
        cursor = conn.cursor()
        
        query = "SELECT * FROM trades"
        params = []
        
        if filters:
            conditions = [f"{k} = ?" for k in filters.keys()]
            query += " WHERE " + " AND ".join(conditions)
            params = list(filters.values())
        
        query += " ORDER BY timestamp DESC"
        
        if limit:
            query += f" LIMIT {limit}"
        
        cursor.execute(query, params)
        trades = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        return trades
    
    def save_backtest_results(self, results):
        """
        Save backtest results
        
        Args:
            results (dict): Backtest metrics and metadata
            
        Returns:
            int: ID of inserted backtest record
        """
        conn = self.connect()
        cursor = conn.cursor()
        
        # Convert config dict to JSON string if present
        if 'config' in results and isinstance(results['config'], dict):
            results['config'] = json.dumps(results['config'])
        
        columns = ', '.join(results.keys())
        placeholders = ', '.join(['?' for _ in results])
        query = f"INSERT INTO backtest_results ({columns}) VALUES ({placeholders})"
        
        cursor.execute(query, list(results.values()))
        backtest_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return backtest_id
    
    def save_strategy_version(self, version_name, config, description=''):
        """
        Save a strategy configuration version
        
        Args:
            version_name (str): Unique version identifier
            config (dict): Strategy configuration parameters
            description (str): Optional description
            
        Returns:
            int: ID of inserted version
        """
        conn = self.connect()
        cursor = conn.cursor()
        
        config_json = json.dumps(config)
        
        cursor.execute('''
            INSERT INTO strategy_versions (version_name, description, config)
            VALUES (?, ?, ?)
        ''', (version_name, description, config_json))
        
        version_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return version_id
    
    def get_strategy_version(self, version_name):
        """
        Retrieve a strategy version
        
        Args:
            version_name (str): Version identifier
            
        Returns:
            dict: Strategy version data including config
        """
        conn = self.connect()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM strategy_versions WHERE version_name = ?
        ''', (version_name,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            version = dict(row)
            version['config'] = json.loads(version['config'])
            return version
        return None


# Convenience function
def get_db():
    """Get database instance"""
    return Database()
