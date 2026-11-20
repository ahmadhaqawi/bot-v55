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
