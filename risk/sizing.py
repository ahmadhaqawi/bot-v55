# risk/sizing.py

import math


def compute_margin(
    equity,
    sl_pct,
    risk_pct,
    leverage,
    use_risk_sizing=True,
    position_size_usdt=50,
    min_margin_usdt=10,
    max_margin_usdt=130,
    max_margin_pct_equity=0.30
):
    """
    حساب حجم الهامش الأساسي حسب نسبة المخاطرة.
    """

    if (
        not math.isfinite(equity)
        or not math.isfinite(sl_pct)
        or equity <= 0
        or sl_pct <= 0
        or leverage <= 0
    ):
        return 0.0

    risk_usd = equity * risk_pct / 100.0

    if use_risk_sizing:
        margin = risk_usd / (sl_pct * leverage)
    else:
        margin = position_size_usdt

    cap = min(
        max_margin_usdt,
        equity * max_margin_pct_equity
    )

    sized = min(cap, margin)

    return sized if sized >= min_margin_usdt else 0.0


def compute_margin_scored(
    equity,
    sl_pct,
    signal_score,
    free_margin,
    risk_pct,
    leverage,
    ai_prob=None,
    ai_active=False,
    min_margin_usdt=10,
    max_margin_usdt=130,
    max_margin_pct_equity=0.30
):
    """
    حساب حجم الصفقة بناءً على جودة الإشارة وAI.
    """

    base = compute_margin(
        equity=equity,
        sl_pct=sl_pct,
        risk_pct=risk_pct,
        leverage=leverage,
        max_margin_usdt=max_margin_usdt,
        max_margin_pct_equity=max_margin_pct_equity,
        min_margin_usdt=min_margin_usdt
    )

    if (
        base <= 0
        or not math.isfinite(free_margin)
        or free_margin <= 0
    ):
        return 0.0

    if ai_active and ai_prob is not None:

        if ai_prob >= 0.70:
            mult = 1.00
        elif ai_prob >= 0.60:
            mult = 0.85
        elif ai_prob >= 0.50:
            mult = 0.65
        else:
            mult = 0.40

    else:

        if signal_score >= 86:
            mult = 1.00

        elif signal_score >= 78:
            mult = 0.85

        elif signal_score >= 70:
            mult = 0.70

        elif signal_score >= 62:
            mult = 0.55

        else:
            mult = 0.40

    sized = min(
        base * mult,
        free_margin
    )

    return sized if sized >= min_margin_usdt else 0.0


def effective_risk_pct(
    equity,
    sl_pct,
    leverage,
    risk_pct,
    max_margin_usdt,
    max_margin_pct_equity
):
    """
    حساب نسبة المخاطرة الفعلية بعد تطبيق حدود الهامش.
    """

    if equity <= 0 or sl_pct <= 0:
        return 0.0

    risk_usd = equity * risk_pct / 100.0

    margin = risk_usd / (
        sl_pct * leverage
    )

    cap = min(
        max_margin_usdt,
        equity * max_margin_pct_equity
    )

    margin = min(
        cap,
        margin
    )

    return (
        margin
        * sl_pct
        * leverage
        / equity
        * 100.0
    )
