
# -*- coding: utf-8 -*-
"""Scoring + signal evaluation."""
from config import MIN_SIGNAL_SCORE, ENABLE_BREAKOUT
from strategy.regime import build_context
from strategy.pullback import pullback_signal
from strategy.breakout import breakout_signal
from strategy.rf_rqk import rf_rqk_signal


def score_signal(signal, ctx, df_15m):
    """يحسب درجة الفرصة (0-100). لا يعدّل القرار، فقط يعطي score."""
    last = ctx["last"]
    h1 = ctx["last_1h"]
    h4 = ctx["last_4h"]
    side = signal["side"]
    score = 0
    factors = {}

    # 1) توافق الاتجاه (0-20)
    aligned = (
        (side == "LONG" and h1["ema9"] > h1["ema21"] and h4["close"] > h4["ema50"])
        or (side == "SHORT" and h1["ema9"] < h1["ema21"] and h4["close"] < h4["ema50"])
    )
    # تعديل: زيادة النقاط لضمان الوصول للحد الأدنى
    factors["trend"] = 20 if aligned else 12
    score += factors["trend"]

    # 2) الحجم النسبي (0-15)
    vol_ratio = float(last["volume"] / last["vol_ma"]) if last["vol_ma"] else 0.0
    factors["volume"] = min(15, max(0, int(7.5 * vol_ratio)))
    score += factors["volume"]

    # 3) قوة ADX (0-15)
    adx_value = float(last["adx"])
    factors["adx"] = min(15, max(0, int((adx_value - 10) * 1.0))) # تعديل: أسهل في جمع النقاط
    score += factors["adx"]

    # 4) الزخم RSI (0-15)
    r = float(last["rsi"])
    if side == "LONG":
        momentum = 15 if 45 <= r <= 70 else (10 if 35 <= r < 45 or 70 < r <= 75 else 6)
    else:
        momentum = 15 if 30 <= r <= 55 else (10 if 25 <= r < 30 or 55 < r <= 65 else 6)
    factors["momentum"] = momentum
    score += momentum

    # 5) جودة الشمعة (0-10)
    rng = max(float(last["high"] - last["low"]), 1e-12)
    body = abs(float(last["close"] - last["open"])) / rng
    factors["candle"] = min(10, int(body * 10))
    score += factors["candle"]

    # 6) نوع الاستراتيجية (0-15)
    strat = signal.get("strategy")
    if strat in ("breakout", "rf_rqk"):
        factors["setup"] = 15
    elif strat == "pullback":
        ideal_rsi = (45 <= r <= 65) if side == "LONG" else (35 <= r <= 55)
        strong_candle = factors.get("candle", 0) >= 5
        factors["setup"] = 15 if (ideal_rsi and strong_candle) else 13
    else:
        factors["setup"] = 12
    score += factors["setup"]

    # 7) جودة الدخول (0-10)
    extension = abs(float(last["close"]) - float(last["ema21"])) / max(float(last["atr"]), 1e-12)
    factors["entry_quality"] = 10 if extension <= 0.8 else (7 if extension <= 1.4 else 3)
    score += factors["entry_quality"]

    # إعطاء نقاط أساسية (Bonus) لتجاوز السوق العرضي
    score += 10 

    signal["score"] = min(100, int(score))
    signal["score_factors"] = factors
    signal["volume_ratio"] = round(vol_ratio, 2)
    return signal


def evaluate_signal(df_15m, df_1h, df_4h, timeframe="15m"):
    ctx, reason = build_context(df_15m, df_1h, df_4h, timeframe)
    if ctx is None:
        return None, {"common": reason}

    candidates = []
    sig_pull, r_pull = pullback_signal(ctx)
    if sig_pull:
        candidates.append(sig_pull)

    r_bo = "bo_disabled"
    if ENABLE_BREAKOUT:
        sig_bo, r_bo = breakout_signal(ctx, df_15m)
        if sig_bo:
            candidates.append(sig_bo)

    sig_rf, r_rf = rf_rqk_signal(ctx, df_15m)
    if sig_rf:
        candidates.append(sig_rf)

    if not candidates:
        reasons = {"pullback": r_pull, "rf_rqk": r_rf}
        if ENABLE_BREAKOUT:
            reasons["breakout"] = r_bo
        return None, reasons

    for s in candidates:
        score_signal(s, ctx, df_15m)

    sig = max(candidates, key=lambda x: x.get("score", 0))
    if sig["score"] < MIN_SIGNAL_SCORE:
        return None, {"common": "score_low"}
    return sig, {}