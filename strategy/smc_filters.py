import os
import numpy as np
import pandas as pd


def detect_liquidity_sweep(df: pd.DataFrame, lookback: int = 20, wick_ratio_min: float = 0.45) -> dict:
    """
    يكتشف سحب السيولة (Liquidity Sweep) على آخر شمعة مغلقة (index -2).
    - للشراء (LONG): كسر قاع آخر `lookback` شمعة بذيل سفلي ثم الإغلاق فوق ذلك القاع.
    - للبيع (SHORT): اختراق قمة آخر `lookback` شمعة بذيل علوي ثم الإغلاق تحت تلك القمة.
    """
    if df is None or len(df) < lookback + 3:
        return {"bullish_sweep": False, "bearish_sweep": False, "sweep_strength": 0.0}

    # نستخدم آخر شمعة مغلقة (-2) لتجنب إعادة الرسم (Repainting) على الشمعة الحية (-1)
    candle = df.iloc[-2]
    window = df.iloc[-(lookback + 2):-2]

    prev_low = float(window["low"].min())
    prev_high = float(window["high"].max())

    o, h, l, c = float(candle["open"]), float(candle["high"]), float(candle["low"]), float(candle["close"])
    candle_range = max(h - l, 1e-12)

    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)

    bullish_sweep = (
        l < prev_low
        and c > prev_low
        and (lower_wick / candle_range) >= wick_ratio_min
    )

    bearish_sweep = (
        h > prev_high
        and c < prev_high
        and (upper_wick / candle_range) >= wick_ratio_min
    )

    strength = 0.0
    if bullish_sweep:
        strength = round((lower_wick / candle_range) * 100, 1)
    elif bearish_sweep:
        strength = round((upper_wick / candle_range) * 100, 1)

    return {
        "bullish_sweep": bullish_sweep,
        "bearish_sweep": bearish_sweep,
        "sweep_strength": strength,
        "swept_level": prev_low if bullish_sweep else (prev_high if bearish_sweep else None),
    }


def detect_fvg(df: pd.DataFrame, lookback_bars: int = 8, min_gap_atr_ratio: float = 0.25) -> dict:
    """
    يكتشف فجوات القيمة العادلة (Fair Value Gap - FVG) غير المغلقة بالكامل في آخر `lookback_bars` شموع،
    ويتحقق مما إذا كان السعر الحالي يختبر منطقة الفجوة (Mitigation / Retest).
    """
    if df is None or len(df) < lookback_bars + 15:
        return {"bullish_fvg": False, "bearish_fvg": False, "in_fvg_zone": False}

    # حساب ATR مبسط لتطبيع حجم الفجوة حسب تقلب العملة
    tr = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ),
    )
    atr = float(tr.rolling(14).mean().iloc[-2])
    if np.isnan(atr) or atr <= 0:
        return {"bullish_fvg": False, "bearish_fvg": False, "in_fvg_zone": False}

    current_close = float(df["close"].iloc[-2])
    current_low = float(df["low"].iloc[-2])
    current_high = float(df["high"].iloc[-2])

    bullish_fvg_zone = None
    bearish_fvg_zone = None

    # فحص آخر الشموع المغلقة لتحديد أحدث FVG صالحة
    start_idx = len(df) - lookback_bars - 2
    for i in range(max(2, start_idx), len(df) - 2):
        c1_high = float(df["high"].iloc[i - 2])
        c1_low = float(df["low"].iloc[i - 2])
        c3_low = float(df["low"].iloc[i])
        c3_high = float(df["high"].iloc[i])

        # Bullish FVG: قاع الشمعة الثالثة أعلى من قمة الشمعة الأولى
        if c3_low > c1_high and (c3_low - c1_high) >= (atr * min_gap_atr_ratio):
            bullish_fvg_zone = (c1_high, c3_low)

        # Bearish FVG: قمة الشمعة الثالثة أدنى من قاع الشمعة الأولى
        if c3_high < c1_low and (c1_low - c3_high) >= (atr * min_gap_atr_ratio):
            bearish_fvg_zone = (c3_high, c1_low)

    in_bullish_fvg = False
    if bullish_fvg_zone is not None:
        low_b, high_b = bullish_fvg_zone
        # السعر يختبر الفجوة صعوداً دون كسرها للأسفل
        in_bullish_fvg = (current_low <= high_b) and (current_close >= low_b)

    in_bearish_fvg = False
    if bearish_fvg_zone is not None:
        low_s, high_s = bearish_fvg_zone
        # السعر يختبر الفجوة هبوطاً دون اختراقها للأعلى
        in_bearish_fvg = (current_high >= low_s) and (current_close <= high_s)

    return {
        "bullish_fvg": bullish_fvg_zone is not None,
        "bearish_fvg": bearish_fvg_zone is not None,
        "in_fvg_zone": in_bullish_fvg or in_bearish_fvg,
        "in_bullish_fvg": in_bullish_fvg,
        "in_bearish_fvg": in_bearish_fvg,
    }


def evaluate_smc_confirmation(df: pd.DataFrame, side: str) -> dict:
    """
    الدالة الرئيسية للدمج في محرك الإشارات:
    - تُرجع: allowed (bool), score_bonus (int), tags (list[str])
    """
    enabled = os.getenv("ENABLE_SMC_FILTER", "false").lower() == "true"
    strict_mode = os.getenv("SMC_STRICT_MODE", "false").lower() == "true"
    bonus_points = int(os.getenv("SMC_SCORE_BONUS", "6"))

    sweep = detect_liquidity_sweep(df)
    fvg = detect_fvg(df)

    side_upper = side.upper()
    has_sweep = sweep["bullish_sweep"] if side_upper == "LONG" else sweep["bearish_sweep"]
    has_fvg = fvg["in_bullish_fvg"] if side_upper == "LONG" else fvg["in_bearish_fvg"]

    tags = []
    if has_sweep:
        tags.append(f"SMC_SWEEP({sweep['sweep_strength']}%)")
    if has_fvg:
        tags.append("SMC_FVG_RETEST")

    # إذا كان الفلتر مطفأً (الوضع الافتراضي أثناء إكمال العينة الحالية)
    if not enabled:
        return {"allowed": True, "score_bonus": 0, "tags": tags, "reason": "smc_disabled"}

    # وضع الفلتر الصارم: يشترط وجود Sweep أو FVG للدخول
    if strict_mode and not (has_sweep or has_fvg):
        return {
            "allowed": False,
            "score_bonus": 0,
            "tags": [],
            "reason": "no_smc_sweep_or_fvg",
        }

    # وضع التعزيز (Bonus Mode): يمنح نقاطاً إضافية ترفع جودة الفرصة
    total_bonus = 0
    if has_sweep:
        total_bonus += bonus_points
    if has_fvg:
        total_bonus += bonus_points

    return {
        "allowed": True,
        "score_bonus": total_bonus,
        "tags": tags,
        "reason": "smc_confirmed" if tags else "smc_neutral",
    }
