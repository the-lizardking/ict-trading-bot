"""
Enhanced ICT Visualization - TradingView Style
With toggles and expiration logic for FVGs and Order Blocks
"""

import sys
from pathlib import Path
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.data_layer.data_loader import load_data
from src.ict_detection.swing_points import detect_swings
from src.ict_detection.fvg_detector import detect_fvgs
from src.ict_detection.order_blocks import detect_order_blocks


def add_expiration_to_fvgs(df, fvgs, max_candles_forward=50):
    """
    Add expiration logic to FVGs
    FVG expires when:
    1. Price fills it (already handled)
    2. OR after X candles without being tested
    """
    for fvg in fvgs:
        if fvg['filled']:
            # Already filled, use fill time as expiration
            fvg['expiration_index'] = fvg['fill_index']
            fvg['expiration_time'] = fvg['fill_time']
        else:
            # Not filled - expire after max_candles_forward
            exp_index = min(fvg['end_index'] + max_candles_forward, len(df) - 1)
            fvg['expiration_index'] = exp_index
            fvg['expiration_time'] = df.index[exp_index]
    
    return fvgs


def add_expiration_to_obs(df, obs, max_candles_forward=100):
    """
    Add expiration logic to Order Blocks
    OB expires when:
    1. Price breaks through it (invalidated)
    2. OR after X candles
    """
    for ob in obs:
        ob_index = ob['index']
        invalidated = False
        
        # Check if price broke through the OB
        for i in range(ob_index + 1, min(ob_index + max_candles_forward, len(df))):
            if ob['type'] == 'bullish':
                # Bullish OB invalidated if price closes below its low
                if df['close'].iloc[i] < ob['low']:
                    ob['expiration_index'] = i
                    ob['expiration_time'] = df.index[i]
                    ob['invalidated'] = True
                    invalidated = True
                    break
            else:  # bearish
                # Bearish OB invalidated if price closes above its high
                if df['close'].iloc[i] > ob['high']:
                    ob['expiration_index'] = i
                    ob['expiration_time'] = df.index[i]
                    ob['invalidated'] = True
                    invalidated = True
                    break
        
        # If not invalidated, expire after max_candles_forward
        if not invalidated:
            exp_index = min(ob_index + max_candles_forward, len(df) - 1)
            ob['expiration_index'] = exp_index
            ob['expiration_time'] = df.index[exp_index]
            ob['invalidated'] = False
    
    return obs


def create_enhanced_chart():
    """Create TradingView-style chart with toggles"""
    
    print("\n📊 Creating enhanced ICT visualization...")
    
    # Load data
    df = load_data('btc_1m_sample.csv')
    df = df.tail(500).copy()
    
    print(f"Using last 500 candles")
    print(f"Date range: {df.index[0]} to {df.index[-1]}")
    
    # Detect all patterns
    print("\nDetecting patterns...")
    df = detect_swings(df, left_bars=5, right_bars=5)
    df, fvgs = detect_fvgs(df, min_gap_size=0)
    df, obs = detect_order_blocks(df, lookback=20)
    
    # Add expiration logic
    fvgs = add_expiration_to_fvgs(df, fvgs, max_candles_forward=50)
    obs = add_expiration_to_obs(df, obs, max_candles_forward=100)
    
    # Filter to only show recent/valid patterns
    active_fvgs = [f for f in fvgs if f['expiration_index'] < len(df)]
    active_obs = [o for o in obs if o['expiration_index'] < len(df)]
    
    print(f"  ✓ Swings: {df['swing_high'].sum()} highs, {df['swing_low'].sum()} lows")
    print(f"  ✓ FVGs: {len(active_fvgs)} active (with expiration)")
    print(f"  ✓ Order Blocks: {len(active_obs)} active (with expiration)")
    
    # Create figure
    fig = go.Figure()
    
    # 1. CANDLESTICKS (always visible)
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['open'],
        high=df['high'],
        low=df['low'],
        close=df['close'],
        name='Price',
        increasing_line_color='#089981',
        decreasing_line_color='#F23645',
        increasing_fillcolor='#089981',
        decreasing_fillcolor='#F23645',
        visible=True
    ))
    
    # 2. SWING HIGHS (toggleable)
    swing_highs = df[df['swing_high']]
    fig.add_trace(go.Scatter(
        x=swing_highs.index,
        y=swing_highs['high'],
        mode='markers',
        marker=dict(
            symbol='triangle-down',
            size=8,
            color='#F23645',
            line=dict(color='#8B0000', width=1)
        ),
        name='Swing Highs',
        visible='legendonly',  # Hidden by default, toggle in legend
        hovertemplate='<b>Swing High</b><br>$%{y:,.2f}<extra></extra>'
    ))
    
    # 3. SWING LOWS (toggleable)
    swing_lows = df[df['swing_low']]
    fig.add_trace(go.Scatter(
        x=swing_lows.index,
        y=swing_lows['low'],
        mode='markers',
        marker=dict(
            symbol='triangle-up',
            size=8,
            color='#089981',
            line=dict(color='#006400', width=1)
        ),
        name='Swing Lows',
        visible='legendonly',  # Hidden by default
        hovertemplate='<b>Swing Low</b><br>$%{y:,.2f}<extra></extra>'
    ))
    
    # 4. Add FVG boxes (with expiration)
    for fvg in active_fvgs:
        color = 'rgba(8, 153, 129, 0.15)' if fvg['type'] == 'bullish' else 'rgba(242, 54, 69, 0.15)'
        line_color = '#089981' if fvg['type'] == 'bullish' else '#F23645'
        
        fig.add_shape(
            type="rect",
            x0=fvg['start_time'],
            x1=fvg['expiration_time'],  # Now expires!
            y0=fvg['gap_low'],
            y1=fvg['gap_high'],
            fillcolor=color,
            line=dict(color=line_color, width=1, dash='dot'),
            layer='below',
            name=f"{'Bullish' if fvg['type'] == 'bullish' else 'Bearish'} FVG",
            legendgroup='fvgs',
            showlegend=False
        )
    
    # Add dummy trace for FVG legend toggle
    fig.add_trace(go.Scatter(
        x=[None], y=[None],
        mode='markers',
        marker=dict(size=10, color='rgba(100, 100, 100, 0.3)', symbol='square'),
        name=f'Fair Value Gaps ({len(active_fvgs)})',
        visible='legendonly',
        showlegend=True,
        legendgroup='fvgs'
    ))
    
    # 5. Add Order Block boxes (with expiration)
    for ob in active_obs:
        color = 'rgba(33, 150, 243, 0.2)' if ob['type'] == 'bullish' else 'rgba(255, 152, 0, 0.2)'
        line_color = '#2196F3' if ob['type'] == 'bullish' else '#FF9800'
        
        fig.add_shape(
            type="rect",
            x0=ob['timestamp'],
            x1=ob['expiration_time'],  # Now expires!
            y0=ob['low'],
            y1=ob['high'],
            fillcolor=color,
            line=dict(color=line_color, width=1.5),
            layer='below',
            name=f"{'Bullish' if ob['type'] == 'bullish' else 'Bearish'} OB",
            legendgroup='obs',
            showlegend=False
        )
    
    # Add dummy trace for OB legend toggle
    fig.add_trace(go.Scatter(
        x=[None], y=[None],
        mode='markers',
        marker=dict(size=10, color='rgba(33, 150, 243, 0.4)', symbol='square'),
        name=f'Order Blocks ({len(active_obs)})',
        visible='legendonly',
        showlegend=True,
        legendgroup='obs'
    ))
    
    # Update layout - TradingView style
    fig.update_layout(
        title={
            'text': 'BTC/USDT · 1m',
            'font': {'size': 16, 'color': '#D1D4DC'},
            'x': 0.01,
            'xanchor': 'left'
        },
        paper_bgcolor='#131722',
        plot_bgcolor='#131722',
        font=dict(color='#D1D4DC', family='Arial, sans-serif'),
        yaxis=dict(
            title='',
            side='right',
            showgrid=True,
            gridcolor='#1E222D',
            gridwidth=1,
            zeroline=False,
            tickformat=',.2f'
        ),
        xaxis=dict(
            title='',
            showgrid=True,
            gridcolor='#1E222D',
            gridwidth=1,
            rangeslider=dict(visible=False),
            type='date'
        ),
        height=800,
        hovermode='x unified',
        hoverlabel=dict(
            bgcolor='#1E222D',
            font=dict(color='#D1D4DC')
        ),
        legend=dict(
            orientation='v',
            yanchor='top',
            y=0.99,
            xanchor='left',
            x=0.01,
            bgcolor='rgba(19, 23, 34, 0.8)',
            bordercolor='#2A2E39',
            borderwidth=1,
            font=dict(size=11)
        ),
        margin=dict(l=0, r=60, t=40, b=0)
    )
    
    # Save to HTML
    output_file = 'ict_enhanced_chart.html'
    fig.write_html(output_file, config={'displayModeBar': True, 'displaylogo': False})
    
    print(f"\n✓ Enhanced chart saved!")
    print(f"   Location: {Path.cwd()}/{output_file}")
    print(f"\n📁 To view: Open 'ict_enhanced_chart.html' in Chrome")
    
    print("\n" + "="*60)
    print("IMPROVEMENTS:")
    print("  ✅ FVGs and OBs now have expiration dates (not infinite)")
    print("  ✅ Click legend items to toggle visibility")
    print("  ✅ TradingView-style dark theme")
    print("  ✅ Cleaner, more professional appearance")
    print("\n" + "HOW TO USE:")
    print("  • Click legend items to show/hide patterns")
    print("  • Boxes end when pattern expires or is invalidated")
    print("  • Zoom/pan with mouse")
    print("="*60 + "\n")


if __name__ == "__main__":
    create_enhanced_chart()
