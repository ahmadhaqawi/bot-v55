# -*- coding: utf-8 -*-
"""Indicators module — 9 functions, no duplication."""
import pandas as pd
import numpy as np


def ema(s, p):
    return s.ewm(span=p, adjust=False).mean()


def rsi(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1 / p, adjust=False).mean()
    l = -d.clip(upper=0).ewm(alpha=1 / p, adjust=False).mean()
    return 100 - (100 / (1 + g / (l + 1e-9)))


def true_range(high, low, close):
    return pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)


def atr(high, low, close, p=14):
    return true_range(high, low, close).ewm(alpha=1 / p, adjust=False).mean()


def adx(high, low, close, p=14):
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr_val = atr(high, low, close, p)
    plus_di = 100 * (plus_dm.ewm(alpha=1 / p, adjust=False).mean() / (atr_val + 1e-9))
    minus_di = 100 * (minus_dm.ewm(alpha=1 / p, adjust=False).mean() / (atr_val + 1e-9))
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)) * 100
    return dx.ewm(alpha=1 / p, adjust=False).mean()


def efficiency_ratio(close_series, period=10):
    change = (close_series - close_series.shift(period)).abs()
    volatility = (close_series - close_series.shift(1)).abs().rolling(window=period).sum()
    return change / (volatility + 1e-9)


def relative_atr(df):
    try:
        series = (df["atr"] / df["close"]).iloc[-102:-2].dropna()
        if len(series) < 60:
            return None
        base = float(series.median())
        cur = float(df["atr"].iloc[-2] / df["close"].iloc[-2])
        return cur / base if base > 0 else None
    except Exception:
        return None


def calculate_range_filter(df, period=100, mult=3.0):
    if len(df) < 3:
        return 0
    src = df["close"].iloc[:-1]
    smooth = (
        src.diff().abs()
        .ewm(span=period, adjust=False).mean()
        .ewm(span=2 * period - 1, adjust=False).mean()
        * mult
    )
    values = src.to_numpy()
    widths = smooth.to_numpy()
    current = values[0]
    previous = current
    for price, width in zip(values[1:], widths[1:]):
        previous = current
        if price > previous:
            current = max(previous, price - width)
        else:
            current = min(previous, price + width)
    if current > previous and values[-1] > current:
        return 1
    if current < previous and values[-1] < current:
        return -1
    return 0


def add_indicators(df):
    if len(df) < 60:
        return df
    df = df.copy()
    df["ema9"] = ema(df["close"], 9)
    df["ema21"] = ema(df["close"], 21)
    df["ema50"] = ema(df["close"], 50)
    df["rsi"] = rsi(df["close"])
    df["adx"] = adx(df["high"], df["low"], df["close"])
    df["atr"] = atr(df["high"], df["low"], df["close"])
    df["vol_ma"] = df["volume"].rolling(20).mean()
    return df
