# ==========================================
# الاستراتيجية
# ==========================================
def detect_regime(df_15m, df_1h, df_4h, timeframe="15m"):
    if len(df_4h) < 50 or len(df_1h) < 50:
        return "unknown"
    last_4h = df_4h.iloc[-2]
    last_1h = df_1h.iloc[-2]
    adx4 = last_4h.get("adx", 0)
    adx1 = last_1h.get("adx", 0)
    ema50_4 = last_4h.get("ema50", last_4h["close"])
    up_4h = last_4h["close"] > ema50_4 and last_4h["ema9"] > last_4h["ema21"]
    down_4h = last_4h["close"] < ema50_4 and last_4h["ema9"] < last_4h["ema21"]

    if timeframe == "5m":
        adx4_strong_eff = REGIME_ADX4_STRONG * 0.85
        adx4_weak_eff = REGIME_ADX4_WEAK * 0.85
        adx1_strong_eff = REGIME_ADX1_STRONG * 0.90
    else:
        adx4_strong_eff = REGIME_ADX4_STRONG
        adx4_weak_eff = REGIME_ADX4_WEAK
        adx1_strong_eff = REGIME_ADX1_STRONG

    if adx4 > adx4_strong_eff and adx1 > adx1_strong_eff:
        if up_4h:
            return "strong_trend_up"
        if down_4h:
            return "strong_trend_down"
    if adx4 > adx4_weak_eff and (up_4h or down_4h):
        return "weak_trend"
    return "range"


def relative_atr(df):
    """نسبة ATR% الحالي إلى وسيط ATR% خلال ~100 شمعة سابقة."""
    try:
        series = (df["atr"] / df["close"]).iloc[-102:-2].dropna()
        if len(series) < 60:
            return None
        base = float(series.median())
        cur = float(df["atr"].iloc[-2] / df["close"].iloc[-2])
        return cur / base if base > 0 else None
    except Exception:
        return None


def build_context(df_15m, df_1h, df_4h, timeframe="15m"):
    """الشروط العامة المشتركة مع معايرة تلقائية لوضع القناص (5m)."""
    if len(df_15m) < 70 or len(df_1h) < 60 or len(df_4h) < 60:
        return None, "insufficient_data"
    last = df_15m.iloc[-2]
    prev = df_15m.iloc[-3]
    last_1h = df_1h.iloc[-2]
    last_4h = df_4h.iloc[-2]
    needed = ["ema9", "ema21", "ema50", "rsi", "adx", "atr", "vol_ma"]
    if any(last.get(k) is None or pd.isna(last.get(k)) for k in needed):
        return None, "indicators_nan"
    price = float(last["close"])
    atr_val = float(last["atr"])
    adx_val = float(last["adx"])
    if not _valid_trigger(price) or not _valid_trigger(atr_val):
        return None, "indicators_nan"

    if timeframe == "5m":
        atr_mult_eff = SNIPER["atr_sl_mult"]
        min_atr_eff = MIN_ATR_PCT * 0.65
        min_adx_eff = MIN_ADX * 0.90
        min_sl_eff = SNIPER["min_sl"]
        max_sl_eff = SNIPER["max_sl"]
        rr_eff = SNIPER["rr"]
    else:
        atr_mult_eff = ATR_SL_MULT
        min_atr_eff = MIN_ATR_PCT
        min_adx_eff = MIN_ADX
        min_sl_eff = MIN_SL_PCT
        max_sl_eff = MAX_SL_PCT
        rr_eff = RR_RATIO

    regime = detect_regime(df_15m, df_1h, df_4h, timeframe)
    if regime == "unknown":
        return None, "insufficient_data"
    if regime == "range":
        return None, "regime_range"
    atr_pct = atr_val / price
    if atr_pct > MAX_ATR_PCT:
        return None, "atr_high"
    rel = relative_atr(df_15m)
    atr_ok = atr_pct >= min_atr_eff or (
        rel is not None and atr_pct >= ATR_HARD_FLOOR and rel >= REL_ATR_RATIO)
    if not atr_ok:
        return None, "atr_low"
    if adx_val < min_adx_eff:
        return None, "low_adx"

    ker_value = None
    er_series = (df_15m["ker_indicator"] if "ker_indicator" in df_15m
                 else efficiency_ratio(df_15m["close"], KER_PERIOD))
    if ENABLE_KER_FILTER:
        ker_recent = er_series.iloc[-4:-1].dropna()
        ker_smoothed = float(ker_recent.median()) if len(ker_recent) >= 2 else 0.0
        min_ker_eff = MIN_KER * 0.55 if regime in ("strong_trend_up", "strong_trend_down") else MIN_KER
        if timeframe == "5m":
            min_ker_eff = max(min_ker_eff, SNIPER["min_ker"])
        if ker_smoothed < min_ker_eff:
            return None, "low_ker"
        ker_value = ker_smoothed
    else:
        v = er_series.iloc[-2] if len(er_series) >= 2 else None
        ker_value = float(v) if (v is not None and not pd.isna(v)) else None

    rf_direction = (df_15m.attrs["range_filter"] if "range_filter" in df_15m.attrs
                    else calculate_range_filter(df_15m))

    long_dir = regime in ("strong_trend_up", "weak_trend") and last_4h["close"] > last_4h["ema50"]
    short_dir = regime in ("strong_trend_down", "weak_trend") and last_4h["close"] < last_4h["ema50"]

    if rf_direction == -1:
        long_dir = False
    if rf_direction == 1:
        short_dir = False

    if not long_dir and not short_dir:
        return None, "direction_mismatch"

    sl_pct = max(min_sl_eff, min(max_sl_eff, atr_mult_eff * atr_val / price))
    return {
        "last": last, "prev": prev, "last_1h": last_1h, "last_4h": last_4h,
        "price": price, "atr_val": atr_val, "ema21": float(last["ema21"]),
        "ema50": float(last["ema50"]), "rsi": float(last["rsi"]),
        "regime": regime, "long_dir": long_dir, "short_dir": short_dir,
        "ker": ker_value, "sl_pct": sl_pct, "tp_pct": sl_pct * rr_eff,
        "candle_ts": int(last["timestamp"]),
    }, None


def _make_signal(side, ctx, strategy):
    p, sp, tp_pct = ctx["price"], ctx["sl_pct"], ctx["tp_pct"]
    if side == "LONG":
        sl, tp = p * (1 - sp), p * (1 + tp_pct)
    else:
        sl, tp = p * (1 + sp), p * (1 - tp_pct)
    return {"side": side, "price": p, "sl_pct": sp, "sl": sl, "tp": tp,
            "regime": ctx["regime"], "strategy": strategy, "ker": ctx.get("ker"),
            "candle_ts": ctx.get("candle_ts", 0)}
