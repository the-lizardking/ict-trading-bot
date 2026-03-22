"""
Visualization script for swing points
Saves an interactive chart as HTML file that you can open in any browser
"""

import sys
from pathlib import Path
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.data_layer.data_loader import load_data
from src.ict_detection.swing_points import detect_swings

def visualize_swing_points():
    """Create interactive chart showing swing points"""
    
    print("\n📊 Creating swing point visualization...")
    
    # Load data
    df = load_data('btc_1m_sample.csv')
    
    # Use last 500 candles (easier to see on chart)
    df = df.tail(500).copy()
    print(f"Using last 500 candles")
    print(f"Date range: {df.index[0]} to {df.index[-1]}")
    
    # Detect swings
    df = detect_swings(df, left_bars=5, right_bars=5)
    
    # Create candlestick chart
    fig = go.Figure()
    
    # Add candlesticks
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['open'],
        high=df['high'],
        low=df['low'],
        close=df['close'],
        name='BTC/USDT',
        increasing_line_color='#26a69a',
        decreasing_line_color='#ef5350'
    ))
    
    # Add swing highs
    swing_highs = df[df['swing_high']]
    if len(swing_highs) > 0:
        fig.add_trace(go.Scatter(
            x=swing_highs.index,
            y=swing_highs['high'],
            mode='markers',
            marker=dict(
                symbol='triangle-down',
                size=12,
                color='red',
                line=dict(color='darkred', width=2)
            ),
            name='Swing High',
            text=[f"High: ${h:,.2f}" for h in swing_highs['high']],
            hovertemplate='<b>Swing High</b><br>%{text}<br>%{x}<extra></extra>'
        ))
    
    # Add swing lows
    swing_lows = df[df['swing_low']]
    if len(swing_lows) > 0:
        fig.add_trace(go.Scatter(
            x=swing_lows.index,
            y=swing_lows['low'],
            mode='markers',
            marker=dict(
                symbol='triangle-up',
                size=12,
                color='lime',
                line=dict(color='darkgreen', width=2)
            ),
            name='Swing Low',
            text=[f"Low: ${l:,.2f}" for l in swing_lows['low']],
            hovertemplate='<b>Swing Low</b><br>%{text}<br>%{x}<extra></extra>'
        ))
    
    # Add BOS markers
    bos_bullish = df[df['bos'] == 'bullish']
    if len(bos_bullish) > 0:
        fig.add_trace(go.Scatter(
            x=bos_bullish.index,
            y=bos_bullish['high'] * 1.002,  # Slightly above
            mode='markers',
            marker=dict(symbol='arrow-up', size=10, color='cyan'),
            name='BOS Bullish',
            text=['BOS ↑'] * len(bos_bullish),
            hovertemplate='<b>Break of Structure (Bullish)</b><br>%{x}<extra></extra>'
        ))
    
    bos_bearish = df[df['bos'] == 'bearish']
    if len(bos_bearish) > 0:
        fig.add_trace(go.Scatter(
            x=bos_bearish.index,
            y=bos_bearish['low'] * 0.998,  # Slightly below
            mode='markers',
            marker=dict(symbol='arrow-down', size=10, color='orange'),
            name='BOS Bearish',
            text=['BOS ↓'] * len(bos_bearish),
            hovertemplate='<b>Break of Structure (Bearish)</b><br>%{x}<extra></extra>'
        ))
    
    # Add CHoCH markers
    choch_b2b = df[df['choch'] == 'bullish_to_bearish']
    if len(choch_b2b) > 0:
        fig.add_trace(go.Scatter(
            x=choch_b2b.index,
            y=choch_b2b['high'] * 1.003,
            mode='markers+text',
            marker=dict(symbol='x', size=15, color='red', line=dict(width=2)),
            name='CHoCH (Bull→Bear)',
            text=['CHoCH'] * len(choch_b2b),
            textposition='top center',
            hovertemplate='<b>Change of Character</b><br>Bullish → Bearish<br>%{x}<extra></extra>'
        ))
    
    choch_b2b = df[df['choch'] == 'bearish_to_bullish']
    if len(choch_b2b) > 0:
        fig.add_trace(go.Scatter(
            x=choch_b2b.index,
            y=choch_b2b['low'] * 0.997,
            mode='markers+text',
            marker=dict(symbol='x', size=15, color='lime', line=dict(width=2)),
            name='CHoCH (Bear→Bull)',
            text=['CHoCH'] * len(choch_b2b),
            textposition='bottom center',
            hovertemplate='<b>Change of Character</b><br>Bearish → Bullish<br>%{x}<extra></extra>'
        ))
    
    # Update layout
    fig.update_layout(
        title={
            'text': 'BTC/USDT - Swing Point Detection<br><sub>ICT Market Structure Analysis</sub>',
            'x': 0.5,
            'xanchor': 'center'
        },
        yaxis_title='Price (USDT)',
        xaxis_title='Time',
        template='plotly_dark',
        height=800,
        hovermode='x unified',
        legend=dict(
            yanchor="top",
            y=0.99,
            xanchor="left",
            x=0.01
        )
    )
    
    # Remove rangeslider
    fig.update_xaxes(rangeslider_visible=False)
    
    # Save to HTML file instead of opening
    output_file = 'swing_chart.html'
    fig.write_html(output_file)
    
    print(f"\n✓ Chart saved successfully!")
    print(f"   Location: {Path.cwd()}/{output_file}")
    print(f"\n📁 To view:")
    print(f"   1. Find 'swing_chart.html' in your project folder")
    print(f"   2. Drag it to Chrome (or any browser)")
    print(f"   3. Interact with the chart (zoom, pan, hover)")
    
    print("\n" + "="*60)
    print("LEGEND:")
    print("  🔻 Red triangles = Swing Highs")
    print("  🔺 Green triangles = Swing Lows")
    print("  ⬆️ Cyan arrows = Bullish Break of Structure")
    print("  ⬇️ Orange arrows = Bearish Break of Structure")
    print("  ❌ Red X = Change of Character (Bull→Bear)")
    print("  ❌ Green X = Change of Character (Bear→Bull)")
    print("="*60 + "\n")

if __name__ == "__main__":
    visualize_swing_points()
