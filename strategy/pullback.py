# Pullback Strategy

def pullback_signal(ctx):
    last, prev, last_1h = ctx["last"], ctx["prev"], ctx["last_1h"]
    price, ema21, ema50, rsi_val = ctx["price"], ctx["ema21"], ctx["ema50"], ctx["rsi"]
    if not (last["volume"] > MIN_VOL_MULT * last["vol_ma"]):
        return None, "low_volume"
    near_ema = (abs(price - ema21) / price < NEAR_EMA21_PCT or
                abs(price - ema50) / price < NEAR_EMA50_PCT)
    if ctx["long_dir"]:
        bullish_reject = last["close"] > last["open"] and (last["low"] < ema21 or prev["low"] < ema21)
        if not (last["ema9"] > last["ema21"] and last_1h["ema9"] > last_1h["ema21"]):
            return None, "ema_misaligned"
        if not near_ema:
            return None, "far_from_ema"
        if not (RSI_LONG_MIN < rsi_val < RSI_LONG_MAX):
            return None, "rsi_out_of_range"
        if not bullish_reject:
            return None, "no_pullback_candle"
        return _make_signal("LONG", ctx, "pullback"), None
    if not ctx["short_dir"]:
        return None, "direction_mismatch"
    bearish_reject = last["close"] < last["open"] and (last["high"] > ema21 or prev["high"] > ema21)
    if not (last["ema9"] < last["ema21"] and last_1h["ema9"] < last_1h["ema21"]):
        return None, "ema_misaligned"
    if not near_ema:
        return None, "far_from_ema"
    if not (RSI_SHORT_MIN < rsi_val < RSI_SHORT_MAX):
        return None, "rsi_out_of_range"
    if not bearish_reject:
        return None, "no_pullback_candle"
    return _make_signal("SHORT", ctx, "pullback"), None
