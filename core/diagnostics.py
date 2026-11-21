# core/diagnostics.py — التشخيص وإحصائيات الرفض والقبول اليومية
import time
import logging
import threading
from datetime import datetime

from config import KSA_TZ, VERBOSE_REJECT_LOG
from telegram.notifier import STRAT_LABEL, short

log = logging.getLogger("bot")

diag_lock = threading.Lock()


def _diag_blank(date=""):
    return {
        "date": date, "evaluated": 0, "signals": 0, "opened": 0, "signals_by": {},
        "reasons": {}, "reasons_pullback": {}, "reasons_breakout": {},
        "reasons_rf_rqk": {}, "blocks": {},
    }


diag = _diag_blank()
_last_reason = {}
loop_info = {"last_cycle_ts": 0.0, "cycles": 0, "last_duration": 0.0, "last_error": "", "alerted": False}
universe_info = {"mode": "", "size": 0, "excluded": {}, "samples": {}, "categories": {}, "ts": 0}

REASON_LABELS = {
    "regime_range": "سوق عرضي (Range)",
    "insufficient_data": "بيانات غير كافية",
    "indicators_nan": "مؤشرات غير جاهزة",
    "atr_low": "تذبذب منخفض (ATR)",
    "atr_high": "تذبذب مرتفع جداً",
    "low_ker": "كفاءة اتجاه ضعيفة (KER)",
    "low_adx": "قوة اتجاه ضعيفة (ADX)",
    "low_volume": "حجم تداول ضعيف",
    "direction_mismatch": "اتجاه 4س غير متوافق",
    "ema_misaligned": "EMA غير متوافقة (15د/1س)",
    "far_from_ema": "السعر بعيد عن EMA",
    "rsi_out_of_range": "RSI خارج النطاق",
    "no_pullback_candle": "لا توجد شمعة ارتداد",
    "max_same_direction": "حد نفس الاتجاه",
    "sector_limit": "حد القطاع",
    "btc_filter": "فلتر اتجاه BTC",
    "spread_high": "سبريد عالي",
    "funding_high": "فندنغ مرتفع (LONG)",
    "funding_negative": "فندنغ سلبي (SHORT)",
    "ticker_unavailable": "تعذر قراءة السعر/السبريد",
    "slippage": "ابتعاد السعر عن الإشارة",
    "signal_expired": "انتهت صلاحية الإشارة الزمنية",
    "margin_too_small": "الهامش أقل من الحد الأدنى",
    "open_failed": "فشل فتح الصفقة",
    "cooldown": "فترة الانتظار بعد الإغلاق",
    "paused": "البوت موقوف (/pause)",
    "equity_unavailable": "تعذر قراءة الرصيد",
    "daily_loss_limit": "حد الخسارة اليومي",
    "daily_profit_target": "هدف الربح اليومي",
    "max_positions": "الحد الأقصى للصفقات",
    "no_data": "لا توجد بيانات شموع",
    "signal": "إشارة ✅",
    "bo_no_breakout": "لا اختراق لقمة/قاع 20 شمعة",
    "bo_trend_mismatch": "اتجاه الساعة لا يدعم الاختراق",
    "bo_low_volume": "حجم الاختراق ضعيف",
    "bo_weak_candle": "شمعة الاختراق ضعيفة الإغلاق",
    "bo_rsi": "RSI خارج نطاق الاختراق",
    "bo_extended": "السعر بعيد عن EMA21 (مطاردة)",
    "bo_disabled": "الاختراق معطّل",
    "rf_rqk_no_signal": "لا توجد إشارة RF+RQK",
    "rf_rqk_trend_mismatch": "اتجاه الساعة لا يدعم RF+RQK",
    "score_low": "درجة الفرصة أقل من الحد المطلوب",
    "ai_rejected": "رفض الذكاء الاصطناعي (احتمال منخفض)",
}


def label(r):
    if r.startswith("signal:"):
        name = r.split(":", 1)[1]
        return f"إشارة ✅ ({STRAT_LABEL.get(name, name)})"
    return REASON_LABELS.get(r, r)


def _diag_roll_locked():
    today = str(datetime.now(KSA_TZ).date())
    if diag["date"] != today:
        diag.clear()
        diag.update(_diag_blank(today))


def note_eval(sym, signal, reasons):
    with diag_lock:
        _diag_roll_locked()
        diag["evaluated"] += 1
        if signal:
            diag["signals"] += 1
            name = signal.get("strategy", "?")
            diag["signals_by"][name] = diag["signals_by"].get(name, 0) + 1
            _last_reason[sym] = (time.time(), f"signal:{name}")
            return
        if "common" in reasons:
            r = reasons["common"]
            diag["reasons"][r] = diag["reasons"].get(r, 0) + 1
            text = label(r)
        else:
            parts = []
            for strat in ("pullback", "breakout", "rf_rqk"):
                if strat in reasons:
                    r = reasons[strat]
                    key = f"reasons_{strat}"
                    diag[key][r] = diag[key].get(r, 0) + 1
                    strat_name = STRAT_LABEL.get(strat, "RF+RQK" if strat == "rf_rqk" else strat)
                    parts.append(f"{strat_name}: {label(r)}")
            text = " | ".join(parts)
        if VERBOSE_REJECT_LOG:
            log.info(f"[REJECT] {short(sym)} → {text}")
        _last_reason[sym] = (time.time(), text)


def note_block(sym, reason):
    with diag_lock:
        _diag_roll_locked()
        diag["blocks"][reason] = diag["blocks"].get(reason, 0) + 1
        _last_reason[sym] = (time.time(), reason)


def note_opened():
    with diag_lock:
        _diag_roll_locked()
        diag["opened"] += 1


def set_last_reason(sym, reason):
    with diag_lock:
        _last_reason[sym] = (time.time(), reason)
