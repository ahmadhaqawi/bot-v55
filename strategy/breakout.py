# strategy/breakout.py — استراتيجية الاختراق (Breakout)
from config import (
    BO_LOOKBACK, BO_VOL_MULT, BO_BODY_POS,
    BO_RSI_LONG_MIN, BO_RSI_LONG_MAX,
    BO_RSI_SHORT_MIN, BO_RSI_SHORT_MAX, BO_MAX_EXT_ATR,
)
from strategy.regime import _make_signal


def breakout_signal(ctx, df_15m):
    last, last_1h = ctx["last"], ctx["last_1h"]
    window = df_15m.iloc[-(BO_LOOKBACK + 2):-2]
    hh, ll = float(window["high"].max()), float(window["low"].min())
    close, high, low = float(last["close"]), float(last["high"]), float(last["low"])
    rng = high - low
    vol_ok = last["volume"] > BO_VOL_MULT * last["vol_ma"]
    if ctx["long_dir"]:
        if not close > hh:
            return None, "bo_no_breakout"
        if not (last_1h["ema9"] > last_1h["ema21"] and last_1h["close"] > last_1h["ema50"]):
            return None, "bo_trend_mismatch"
        if not vol_ok:
            return None, "bo_low_volume"
        if rng <= 0 or (close - low) / rng < BO_BODY_POS:
            return None, "bo_weak_candle"
        if not (BO_RSI_LONG_MIN < ctx["rsi"] < BO_RSI_LONG_MAX):
            return None, "bo_rsi"
        if close - ctx["ema21"] > BO_MAX_EXT_ATR * ctx["atr_val"]:
            return None, "bo_extended"
        return _make_signal("LONG", ctx, "breakout"), None
    if not ctx["short_dir"]:
        return None, "direction_mismatch"
    if not close < ll:
        return None, "bo_no_breakout"
    if not (last_1h["ema9"] < last_1h["ema21"] and last_1h["close"] < last_1h["ema50"]):
        return None, "bo_trend_mismatch"
    if not vol_ok:
        return None, "bo_low_volume"
    if rng <= 0 or (high - close) / rng < BO_BODY_POS:
        return None, "bo_weak_candle"
    if not (BO_RSI_SHORT_MIN < ctx["rsi"] < BO_RSI_SHORT_MAX):
        return None, "bo_rsi"
    if ctx["ema21"] - close > BO_MAX_EXT_ATR * ctx["atr_val"]:
        return None, "bo_extended"
    return _make_signal("SHORT", ctx, "breakout"), None
