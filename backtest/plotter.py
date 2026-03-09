import os
import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


logger = logging.getLogger("BacktestPlotter")


def _compute_macd_series(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    if close.empty:
        return pd.DataFrame(index=close.index, data={"macd": [], "signal": [], "hist": []})
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist}, index=close.index)


def _orders_to_index_series(df: pd.DataFrame, orders: List[Dict], side: str) -> pd.Series:
    """Return a Series aligned to df.index with prices only at order times for given side."""
    s = pd.Series(index=df.index, dtype=float)
    for tr in orders or []:
        if str(tr.get("side", "")).upper() != side:
            continue
        t = tr.get("time"); p = tr.get("price")
        if t is None or pd.isna(p):
            continue
        ts = pd.to_datetime(t)
        if ts in s.index:
            s.loc[ts] = float(p)
        else:
            # Nearest index fallback
            try:
                loc = s.index.get_indexer([ts], method="nearest")[0]
                if loc >= 0:
                    s.iloc[loc] = float(p)
            except Exception:
                pass
    return s


def plot_backtest(
    df: pd.DataFrame,
    orders: List[Dict],
    title: str,
    outfile: str,
    macd_params: Optional[Dict[str, int]] = None,
) -> Optional[str]:
    """
    Save a PNG with 2 panels: Price (candlesticks + buy/sell markers) and MACD.
    TradingView-like spacing (no gaps): bars are rendered on an integer axis.
    Returns the outfile path or None if plotting is unavailable.
    """
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except Exception as e:
        logger.warning(f"Matplotlib unavailable. Skipping plot: {e}")
        return None

    if df.empty:
        logger.warning("No data to plot")
        return None

    # Ensure required columns
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            logger.warning(f"Missing column '{col}' in data. Skipping plot.")
            return None

    macd_cfg = {"fast": 12, "slow": 26, "signal": 9}
    if macd_params:
        macd_cfg.update({k: int(v) for k, v in macd_params.items() if k in macd_cfg})

    macd_df = _compute_macd_series(df["close"], **macd_cfg)

    # Prepare figure
    plt.close("all")
    fig = plt.figure(figsize=(12, 7), constrained_layout=True)
    gs = fig.add_gridspec(3, 1, height_ratios=[2.0, 1.0, 0.1])
    ax_price = fig.add_subplot(gs[0, 0])
    ax_macd = fig.add_subplot(gs[1, 0])

    # --- Candlesticks on integer x to remove gaps ---
    n = len(df)
    x = np.arange(n)
    w = 0.7  # candle body width
    for xi, (_, row) in zip(x, df.iterrows()):
        o = float(row["open"]); h = float(row["high"]); l = float(row["low"]); c = float(row["close"])
        color = "#16a34a" if c >= o else "#dc2626"
        # Wick
        ax_price.vlines(xi, l, h, color=color, linewidth=0.8, zorder=1)
        # Body
        y = min(o, c); height = max(abs(c - o), 1e-8)
        rect = Rectangle((xi - w / 2, y), w, height, facecolor=color, edgecolor=color, linewidth=0.6, alpha=0.85, zorder=2)
        ax_price.add_patch(rect)

    # --- Buy/Sell markers aligned to integer x ---
    buy_s = _orders_to_index_series(df, orders, "BUY")
    sell_s = _orders_to_index_series(df, orders, "SELL")
    if not buy_s.empty:
        bx = np.where(~buy_s.isna())[0]
        by = buy_s.dropna().values
        if len(bx) > 0:
            ax_price.scatter(bx, by, marker="^", s=48, c="#16a34a", edgecolors="black", linewidths=0.5, zorder=3, label="BUY")
    if not sell_s.empty:
        sx = np.where(~sell_s.isna())[0]
        sy = sell_s.dropna().values
        if len(sx) > 0:
            ax_price.scatter(sx, sy, marker="v", s=48, c="#dc2626", edgecolors="black", linewidths=0.5, zorder=3, label="SELL")

    ax_price.set_title(title)
    ax_price.set_ylabel("Price")
    ax_price.grid(True, linestyle=":", alpha=0.3)
    if (len(buy_s.dropna()) + len(sell_s.dropna())) > 0:
        ax_price.legend(loc="upper left")

    # --- MACD (lines + histogram) on same integer x ---
    ax_macd.plot(x, macd_df["macd"].values, label="MACD", color="#2563eb", linewidth=1.0)
    ax_macd.plot(x, macd_df["signal"].values, label="Signal", color="#f59e0b", linewidth=1.0)
    hist_colors = ["#16a34a" if v >= 0 else "#dc2626" for v in macd_df["hist"].values]
    ax_macd.bar(x, macd_df["hist"].values, color=hist_colors, alpha=0.4)
    ax_macd.axhline(0, color="#6b7280", linewidth=0.7)
    ax_macd.set_ylabel("MACD")
    ax_macd.grid(True, linestyle=":", alpha=0.3)
    ax_macd.legend(loc="upper left")

    # X tick labels sampled to avoid clutter (format timestamps)
    tick_step = max(1, n // 8)
    tick_idx = np.arange(0, n, tick_step)
    tick_labels = [df.index[i].strftime('%Y-%m-%d %H:%M') for i in tick_idx]
    ax_macd.set_xticks(tick_idx)
    ax_macd.set_xticklabels(tick_labels, rotation=30, ha='right')

    # Output
    os.makedirs(os.path.dirname(outfile), exist_ok=True)
    fig.savefig(outfile, dpi=160)
    plt.close(fig)
    logger.info(f"Chart saved: {outfile}")
    return outfile


def plot_backtest_interactive(
    df: pd.DataFrame,
    orders: List[Dict],
    title: str,
    outfile_html: str,
    macd_params: Optional[Dict[str, int]] = None,
) -> Optional[str]:
    """
    Save an interactive HTML chart (Plotly): Candlestick + MACD + Buy/Sell.
    No gaps via categorical x-axis (stringified timestamps).
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except Exception as e:
        logger.warning(f"Plotly unavailable. Skipping interactive plot: {e}")
        return None

    if df.empty:
        return None

    macd_cfg = {"fast": 12, "slow": 26, "signal": 9}
    if macd_params:
        macd_cfg.update({k: int(v) for k, v in macd_params.items() if k in macd_cfg})
    macd_df = _compute_macd_series(df["close"], **macd_cfg)

    cats = [ts.strftime('%Y-%m-%d %H:%M') for ts in df.index]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3])

    # Price
    fig.add_trace(
        go.Candlestick(x=cats, open=df["open"], high=df["high"], low=df["low"], close=df["close"], name="Price"),
        row=1, col=1
    )

    # Markers
    buy_s = _orders_to_index_series(df, orders, "BUY").dropna()
    sell_s = _orders_to_index_series(df, orders, "SELL").dropna()
    if not buy_s.empty:
        bx = [cats[i] for i in np.where(df.index.isin(buy_s.index))[0]]
        fig.add_trace(go.Scatter(x=bx, y=buy_s.values, mode="markers", name="BUY",
                                 marker=dict(symbol="triangle-up", size=10, color="#16a34a", line=dict(color="black", width=0.5))),
                      row=1, col=1)
    if not sell_s.empty:
        sx = [cats[i] for i in np.where(df.index.isin(sell_s.index))[0]]
        fig.add_trace(go.Scatter(x=sx, y=sell_s.values, mode="markers", name="SELL",
                                 marker=dict(symbol="triangle-down", size=10, color="#dc2626", line=dict(color="black", width=0.5))),
                      row=1, col=1)

    # MACD
    fig.add_trace(go.Scatter(x=cats, y=macd_df["macd"], name="MACD", line=dict(color="#2563eb", width=1)), row=2, col=1)
    fig.add_trace(go.Scatter(x=cats, y=macd_df["signal"], name="Signal", line=dict(color="#f59e0b", width=1)), row=2, col=1)
    fig.add_trace(go.Bar(x=cats, y=macd_df["hist"], name="Hist", marker_color=["#16a34a" if v >= 0 else "#dc2626" for v in macd_df["hist"]]), row=2, col=1)

    fig.update_layout(
        title=title,
        xaxis=dict(type="category", showgrid=False, rangeslider=dict(visible=True)),
        xaxis2=dict(type="category", showgrid=False),
        yaxis_title="Price",
        yaxis2_title="MACD",
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )

    os.makedirs(os.path.dirname(outfile_html), exist_ok=True)
    fig.write_html(outfile_html, include_plotlyjs="cdn")
    logger.info(f"Interactive chart saved: {outfile_html}")
    return outfile_html
