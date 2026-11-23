# strategy/rf_rqk.py — استراتيجية Range Filter + Rational Quadratic Kernel (RQK)
from indicators import calculate_zp_rf_rqk_signal
from strategy.regime import _make_signal


def rf_rqk_signal(ctx, df_15m):
    last_1h = ctx["last_1h"]
    regime = ctx.get("regime", "")
    sig_dir = calculate_zp_rf_rqk_signal(df_15m)

    if sig_dir == 1 and ctx["long_dir"]:
        if regime != "strong_trend_up":
            return None, "rf_rqk_weak_or_non_strong_regime"
        if not (last_1h["ema9"] > last_1h["ema21"] and last_1h["close"] > last_1h["ema50"]):
            return None, "rf_rqk_1h_trend_mismatch"
        return _make_signal("LONG", ctx, "rf_rqk"), None

    if sig_dir == -1 and ctx["short_dir"]:
        if regime != "strong_trend_down":
            return None, "rf_rqk_weak_or_non_strong_regime"
        if not (last_1h["ema9"] < last_1h["ema21"] and last_1h["close"] < last_1h["ema50"]):
            return None, "rf_rqk_1h_trend_mismatch"
        return _make_signal("SHORT", ctx, "rf_rqk"), None

    return None, "rf_rqk_no_signal"
