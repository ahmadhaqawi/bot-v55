# strategy/rf_rqk.py — استراتيجية Range Filter + Rational Quadratic Kernel (RQK)
from indicators import calculate_zp_rf_rqk_signal
from strategy.regime import _make_signal


def rf_rqk_signal(ctx, df_15m):
    last_1h = ctx["last_1h"]
    sig_dir = calculate_zp_rf_rqk_signal(df_15m)

    if sig_dir == 1 and ctx["long_dir"]:
        # تعديل: إزالة شرط ترند الساعة للسماح بالمزيد من الإشارات
        # if not (last_1h["ema9"] > last_1h["ema21"] and last_1h["close"] > last_1h["ema50"]):
        #     return None, "rf_rqk_trend_mismatch"
        return _make_signal("LONG", ctx, "rf_rqk"), None

    if sig_dir == -1 and ctx["short_dir"]:
        # تعديل: إزالة شرط ترند الساعة
        # if not (last_1h["ema9"] < last_1h["ema21"] and last_1h["close"] < last_1h["ema50"]):
        #     return None, "rf_rqk_trend_mismatch"
        return _make_signal("SHORT", ctx, "rf_rqk"), None

    return None, "rf_rqk_no_signal"