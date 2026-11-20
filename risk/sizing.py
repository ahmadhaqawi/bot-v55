# Risk Sizing

def compute_margin(equity, sl_pct, risk_pct=None, leverage=None):
    """الهامش الأساسي حسب المخاطرة والمود (Swing أو Sniper)."""
    rp = risk_pct if risk_pct is not None else RISK_PCT_PER_TRADE
    lev = leverage if leverage is not None else LEVERAGE
    if (not math.isfinite(equity) or not math.isfinite(sl_pct)
            or equity <= 0 or sl_pct <= 0 or lev <= 0):
        return 0.0
    risk_usd = equity * rp / 100.0
    margin = risk_usd / (sl_pct * lev) if USE_RISK_SIZING else POSITION_SIZE_USDT
    cap = min(MAX_MARGIN_USDT, equity * MAX_MARGIN_PCT_EQUITY)
    sized = min(cap, margin)
    return sized if sized >= MIN_MARGIN_USDT else 0.0


def compute_margin_scored(equity, sl_pct, signal_score, free_margin, ai_prob=None, risk_pct=None, leverage=None):
    base = compute_margin(equity, sl_pct, risk_pct, leverage)
    if base <= 0 or not math.isfinite(free_margin) or free_margin <= 0:
        return 0.0
    if AI_IS_ACTIVE and ai_prob is not None:
        if ai_prob >= 0.70:
            mult = 1.00
        elif ai_prob >= 0.60:
            mult = 0.85
        elif ai_prob >= 0.50:
            mult = 0.65
        else:
            mult = 0.40
    else:
        mult = next(m for threshold, m in ((86, 1.0), (78, 0.85), (70, 0.70), (62, 0.55), (0, 0.40))
                    if max(0, signal_score) >= threshold)
    sized = min(base * mult, free_margin)
    return sized if sized >= MIN_MARGIN_USDT else 0.0
