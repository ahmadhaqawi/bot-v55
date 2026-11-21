# telegram/commands.py — نصوص الأوامر (/status /pos /risk ...) والموزّع handle_command
import json
import time
import logging

from config import (
    ATR_HARD_FLOOR,
    BO_LOOKBACK,
    BO_MAX_EXT_ATR,
    BO_VOL_MULT,
    DAILY_LOSS_LIMIT_PCT,
    DAILY_PROFIT_TARGET_PCT,
    ENABLE_BREAKOUT,
    ENABLE_KER_FILTER,
    ENABLE_SNIPER,
    ENTRY_MODE,
    KER_PERIOD,
    LEVERAGE,
    MAX_MARGIN_PCT_EQUITY,
    MAX_MARGIN_USDT,
    MAX_OPEN_POSITIONS,
    MAX_SAME_DIRECTION,
    MAX_SPREAD_PCT,
    MAX_UNIVERSE_SYMBOLS,
    MIN_ADX,
    MIN_ATR_PCT,
    MIN_KER,
    MIN_SIGNAL_SCORE,
    MIN_VOL_MULT,
    NEAR_EMA21_PCT,
    NEAR_EMA50_PCT,
    PROFILE_NAME,
    REGIME_ADX1_STRONG,
    REGIME_ADX4_STRONG,
    REGIME_ADX4_WEAK,
    REL_ATR_RATIO,
    RISK_PCT_PER_TRADE,
    RSI_LONG_MAX,
    RSI_LONG_MIN,
    RSI_SHORT_MAX,
    RSI_SHORT_MIN,
    SNIPER,
    UNIVERSE_MODE,
    USE_AI_ENTRY_FILTER,
    VERSION,
    WATCHDOG_SEC,
)
from core.state import (
    _last_spread,
    daily_snapshot,
    meta,
    open_positions_snapshot,
    set_meta,
    state,
    state_lock,
    trade_lock,
)
from core.journal import paper_stats_text, stats_text
from core.diagnostics import _last_reason, diag, diag_lock, label, loop_info, universe_info
from core.trade_finalize import finalize_trade, floating_pnl_cached
from exchange import contract_size, get_current_price, get_equity
from market_data import _btc_bias_cache
from protection import close_full
from telegram.notifier import STRAT_LABEL, fmt, send, short
from backup import backup_to_telegram
from risk.sizing import effective_risk_pct
from ai.local_ai import ai_analyze_trades

import ai.local_ai as ai_state   # AI_IS_ACTIVE يتغير بعد التحميل: نقرأه كخاصية وليس باستيراد الاسم

log = logging.getLogger("bot")


def unrealized(ex, sym, v):
    price = get_current_price(ex, sym) or v["entry"]
    d = 1 if v["position"] == "LONG" else -1
    upnl = (price - v["entry"]) * d * v["contracts"] * contract_size(ex, sym)
    return price, upnl


def flags_text(v):
    f = ""
    if v.get("mode") == "sniper":
        f += " 🎯"
    if v.get("partial_2_taken"):
        f += " 💰💰"
    elif v.get("partial_1_taken"):
        f += " 💰"
    if v.get("breakeven_hit"):
        f += " 🛡️"
    return f


def loop_age_text():
    last = loop_info["last_cycle_ts"]
    if not last:
        return "لم تبدأ بعد"
    age = int(time.time() - last)
    return f"قبل {age}ث" + (" ⚠️" if age > WATCHDOG_SEC else " ✅")


def status_text(ex):
    snap = daily_snapshot()
    equity = get_equity(ex, force=True)
    opens = open_positions_snapshot()
    total_float = 0.0
    lines_pos = []
    for sym, v in opens.items():
        price, upnl = unrealized(ex, sym, v)
        total_float += upnl
        lines_pos.append(f"• <code>{short(sym)}</code> {v['position']} | {fmt(v['entry'])} → {fmt(price)} | {upnl:+.2f}{flags_text(v)}")
    with diag_lock:
        ev, sg, op = diag["evaluated"], diag["signals"], diag["opened"]
    bias = _btc_bias_cache["value"] if _btc_bias_cache["ts"] else "لم يُحسب بعد"
    lines = [
        f"📊 <b>حالة البوت {VERSION}</b> {'⏸️' if meta.get('paused') else '▶️'}", "━━━━━━━━━━━━━━━",
        f"💼 الرصيد: {equity:,.2f} USDT",
        f"📅 المحقق اليوم: {snap['value']:+.2f} USDT | صفقات: {snap['trades']} | رابحة: {snap['wins']}",
        f"📈 <b>الربح العائم: {total_float:+.2f} USDT</b>",
        f"🔄 آخر دورة: {loop_age_text()} | دورات: {loop_info['cycles']}",
        f"🔎 اليوم: تقييم {ev} | إشارات {sg} | فُتحت {op}  (/why للتفاصيل)",
        f"🐋 BTC Bias: {bias} | ⚙️ {PROFILE_NAME}",
        "━━━━━━━━━━━━━━━",
    ]
    lines.extend(lines_pos if lines_pos else ["لا توجد صفقات مفتوحة."])
    return "\n".join(lines)


def why_text(arg, active_symbols):
    if arg:
        sym = find_symbol(arg, active_symbols)
        if not sym:
            return "عملة غير معروفة."
        with diag_lock:
            item = _last_reason.get(sym)
        if not item:
            return f"لا يوجد تقييم بعد لـ {short(sym)}."
        ts, r = item
        txt = f"🔍 <b>{short(sym)}</b>\nآخر نتيجة: {label(r)}\nقبل {int((time.time() - ts) / 60)} دقيقة"
        sp = _last_spread.get(sym)
        if sp:
            txt += f"\nآخر سبريد: {sp[1] * 100:.3f}% (الحد {MAX_SPREAD_PCT * 100:.2f}%)"
        return txt
    with diag_lock:
        d = json.loads(json.dumps(diag))
    ev = d["evaluated"]
    sig_txt = " | ".join(f"{STRAT_LABEL.get(k, k)} {v}" for k, v in d.get("signals_by", {}).items()) or "—"
    lines = [
        f"🔎 <b>لماذا لا توجد صفقات؟</b> — {d['date']}", "━━━━━━━━━━━━━━━",
        f"📊 تقييمات: {ev}",
        f"✅ إشارات: {d['signals']} ({sig_txt}) | 🟢 فُتحت: {d['opened']}",
        f"⚙️ البروفايل: <b>{PROFILE_NAME}</b> | الاختراق: {'مفعّل' if ENABLE_BREAKOUT else
