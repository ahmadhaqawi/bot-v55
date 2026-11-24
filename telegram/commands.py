# telegram/commands.py — نصوص الأوامر (/status /pos /risk ...) والموزّع handle_command
import json
import sys
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


def _blocked_summary_text():
    try:
        main_mod = sys.modules.get("main")
        if main_mod is None or not hasattr(main_mod, "get_blocked_symbols_summary"):
            import main as main_mod
        return main_mod.get_blocked_symbols_summary()
    except Exception as e:
        log.warning(f"[CMD BLOCKED SUMMARY] {e}")
        return "⚠️ تعذر جلب قائمة العملات المحجوبة حالياً."


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
        f"⚙️ البروفايل: <b>{PROFILE_NAME}</b> | الاختراق: {'مفعّل' if ENABLE_BREAKOUT else 'معطّل'} | KER: {'مفعّل' if ENABLE_KER_FILTER else 'معطّل'}",
    ]
    if meta.get("paused"):
        lines.append("⏸️ <b>البوت موقوف (/resume)</b>")
    if ev:
        lines += ["", "<b>الأسباب العامة:</b>"]
        for r, n in sorted(d["reasons"].items(), key=lambda x: -x[1])[:8]:
            lines.append(f"• {label(r)}: {n} ({n / ev * 100:.0f}%)")
        staged = sum(d.get("reasons_pullback", {}).values())
        if staged:
            lines.append(f"\nوصلت للاستراتيجيات: {staged + d['signals']} شمعة")
        for key, title in (("reasons_pullback", "الارتداد"), ("reasons_breakout", "الاختراق")):
            items = sorted(d.get(key, {}).items(), key=lambda x: -x[1])[:4]
            if items:
                lines += ["", f"<b>{title}:</b>"]
                for r, n in items:
                    lines.append(f"• {label(r)}: {n}")
    else:
        lines.append("\nلا توجد تقييمات بعد (انتظر إغلاق شمعة 15 دقيقة).")
    if d["blocks"]:
        lines += ["", "<b>إشارات حُجبت بعد ظهورها:</b>"]
        for r, n in sorted(d["blocks"].items(), key=lambda x: -x[1]):
            lines.append(f"• {label(r)}: {n}")
    lines.append("\nلتفاصيل عملة: /why BTC")
    return "\n".join(lines)


def universe_text(active_symbols):
    u = universe_info
    names = ", ".join(short(x).split("/")[0] for x in list(active_symbols)[:70])
    lines = [
        f"🪙 <b>العملات المفحوصة</b> — {u.get('mode') or '—'} | العدد: {len(active_symbols)}",
        "━━━━━━━━━━━━━━━", names or "—",
    ]
    if u.get("excluded"):
        lines += ["", "<b>مستبعد:</b>"]
        for k, n in u["excluded"].items():
            smp = u.get("samples", {}).get(k) or []
            lines.append(f"• {k}: {n}" + (f" ({', '.join(smp)})" if smp else ""))
    if u.get("categories"):
        lines.append(f"\nالتصنيفات: {u['categories']}")
    lines.append("\nلتقييد: UNIVERSE_MODE=core أو EXCLUDE_BASES في Render.")
    return "\n".join(lines)


def config_text():
    return "\n".join([
        f"⚙️ <b>الإعدادات</b> — البروفايل: <b>{PROFILE_NAME}</b> | نمط الدخول: <b>{ENTRY_MODE}</b>",
        "━━━━━━━━━━━━━━━",
        f"ATR الأدنى: {MIN_ATR_PCT * 100:.2f}%",
        f"ATR النسبي: ≥ {REL_ATR_RATIO:g}× (حد أدنى {ATR_HARD_FLOOR * 100:.2f}%)"
        + (" — معطّل" if REL_ATR_RATIO >= 50 else ""),
        f"KER: {'مفعّل' if ENABLE_KER_FILTER else 'معطّل'} | الحد {MIN_KER:g} | الفترة {KER_PERIOD} | ADX: {MIN_ADX:g}",
        f"ADX 4س قوي/ضعيف: {REGIME_ADX4_STRONG:g}/{REGIME_ADX4_WEAK:g} | 1س قوي: {REGIME_ADX1_STRONG:g}",
        f"الارتداد — حجم ≥ {MIN_VOL_MULT:g}× | RSI L {RSI_LONG_MIN:g}–{RSI_LONG_MAX:g} | S {RSI_SHORT_MIN:g}–{RSI_SHORT_MAX:g}",
        f"القرب من EMA21/50: {NEAR_EMA21_PCT * 100:.1f}% / {NEAR_EMA50_PCT * 100:.1f}%",
        f"الاختراق: {'مفعّل' if ENABLE_BREAKOUT else 'معطّل'} | نافذة {BO_LOOKBACK} | حجم ≥ {BO_VOL_MULT:g}× | أقصى مطاردة {BO_MAX_EXT_ATR:g}×ATR",
        f"أقصى سبريد: {MAX_SPREAD_PCT * 100:.2f}%",
        f"المخاطرة: {RISK_PCT_PER_TRADE}% | رافعة {LEVERAGE}x | سقف الهامش {MAX_MARGIN_USDT:.0f}",
        f"حد الدرجة: {MIN_SIGNAL_SCORE}/100 | عملات: {UNIVERSE_MODE} (حتى {MAX_UNIVERSE_SYMBOLS})",
        f"أقصى صفقات: {MAX_OPEN_POSITIONS} | نفس الاتجاه: {MAX_SAME_DIRECTION}",
        f"القناص: {'مفعّل' if ENABLE_SNIPER else 'معطّل'} | حد الدرجة {SNIPER['min_score']} | رافعة {SNIPER['leverage']}x | مخاطرة {SNIPER['risk_pct']}% | مدة {SNIPER['max_hold_minutes']}د",
        f"AI المحلي: {'نشط ✅' if ai_state.AI_IS_ACTIVE else 'معطّل'} | فلتر الدخول: {'مفعّل' if USE_AI_ENTRY_FILTER else 'تشخيص فقط'}",
        "━━━━━━━━━━━━━━━",
        "للتعديل: متغيرات البيئة في Render ثم Redeploy.",
    ])


def positions_text(ex):
    opens = open_positions_snapshot()
    if not opens:
        return "📭 لا توجد صفقات مفتوحة."
    lines = ["📊 <b>الصفقات المفتوحة</b>", "━━━━━━━━━━━━━━━"]
    total = 0.0
    for sym, v in opens.items():
        price, upnl = unrealized(ex, sym, v)
        total += upnl
        pct = (upnl / v["margin"] * 100) if v.get("margin") else 0.0
        mins = int((time.time() - v["opened_ts"]) / 60) if v.get("opened_ts") else 0
        duration = f"{mins // 60}س {mins % 60}د" if mins >= 60 else f"{mins}د"
        risk = v.get("initial_risk") or 0
        d = 1 if v["position"] == "LONG" else -1
        r_now = ((price - v["entry"]) * d / risk) if risk else 0.0
        score = v.get("signal_score", 0)
        lines.append(
            f"🪙 <b>{short(sym)}</b> ({v['position']}) | Score: {score}\n"
            f"📥 {fmt(v['entry'])} → 📤 {fmt(price)}\n"
            f"💰 العائم: <b>{upnl:+.2f} USDT</b> ({pct:+.1f}%) | {r_now:+.2f}R\n"
            f"🧾 جزئيات: {v.get('realized_pnl', 0.0):+.2f}\n"
            f"🛑 {fmt(v['sl']) if v.get('sl') else '—'} | 🎯 {fmt(v['tp']) if v.get('tp') else '—'}\n"
            f"⏱ {duration}{flags_text(v)}\n────────────────"
        )
    lines.append(f"\n📈 <b>إجمالي العائم: {total:+.2f} USDT</b>")
    return "\n".join(lines)


def risk_text(ex):
    snap = daily_snapshot()
    equity = get_equity(ex, force=True)
    opens = open_positions_snapshot()
    longs = sum(1 for v in opens.values() if v["position"] == "LONG")
    shorts = sum(1 for v in opens.values() if v["position"] == "SHORT")
    loss_limit = equity * DAILY_LOSS_LIMIT_PCT / 100.0
    profit_target = equity * DAILY_PROFIT_TARGET_PCT / 100.0
    floating = floating_pnl_cached(ex)
    status = "⏸️ متوقف" if meta.get("paused") else "▶️ يعمل"
    r1 = effective_risk_pct(equity, 0.008, LEVERAGE, RISK_PCT_PER_TRADE, MAX_MARGIN_USDT, MAX_MARGIN_PCT_EQUITY)
    r2 = effective_risk_pct(equity, 0.015, LEVERAGE, RISK_PCT_PER_TRADE, MAX_MARGIN_USDT, MAX_MARGIN_PCT_EQUITY)
    r3 = effective_risk_pct(equity, 0.025, LEVERAGE, RISK_PCT_PER_TRADE, MAX_MARGIN_USDT, MAX_MARGIN_PCT_EQUITY)
    return "\n".join([
        "🛡️ <b>ملخص المخاطر</b>", "━━━━━━━━━━━━━━━",
        f"💼 الرصيد: <b>{equity:,.2f} USDT</b>",
        f"📊 الحالة: {status}", "",
        f"🎯 المخاطرة: <b>{RISK_PCT_PER_TRADE}%</b> | سقف الهامش {MAX_MARGIN_USDT:.0f}",
        f"📐 الفعلية عند 0.8%/1.5%/2.5%: <b>{r1:.2f}% / {r2:.2f}% / {r3:.2f}%</b>",
        f"📉 حد الخسارة: {DAILY_LOSS_LIMIT_PCT}% ({loss_limit:.1f} USDT)",
        f"📈 هدف الربح: {DAILY_PROFIT_TARGET_PCT}% ({profit_target:.1f} USDT)", "",
        f"🔢 المفتوحة: {len(opens)} / {MAX_OPEN_POSITIONS}",
        f"🟢 LONG: {longs} | 🔴 SHORT: {shorts}",
        f"📅 اليوم: <b>{snap['value']:+.2f} USDT</b> | العائم: {floating:+.2f}",
        "━━━━━━━━━━━━━━━",
    ])


def daily_text(ex):
    snap = daily_snapshot()
    equity = get_equity(ex, force=True)
    total_upnl = sum(unrealized(ex, s, v)[1] for s, v in open_positions_snapshot().items())
    wr = (snap["wins"] / snap["trades"] * 100) if snap["trades"] else 0
    return "\n".join([
        f"📅 <b>ملخص اليوم</b> — {snap['date']}", "━━━━━━━━━━━━━━━",
        f"💰 المحقق: <b>{snap['value']:+.2f} USDT</b>",
        f"📈 العائم: <b>{total_upnl:+.2f} USDT</b>",
        f"📊 الإجمالي: <b>{snap['value'] + total_upnl:+.2f} USDT</b>", "",
        f"🔢 الصفقات: {snap['trades']}",
        f"✅ الرابحة: {snap['wins']} ({wr:.0f}%)",
        f"💼 الرصيد: {equity:,.2f} USDT", "━━━━━━━━━━━━━━━",
    ])


def find_symbol(arg, active_symbols):
    arg = arg.upper().replace("/USDT", "").replace("USDT", "")
    for s in list(active_symbols):
        if s.startswith(arg + "/"):
            return s
    return None


def handle_command(ex, text, chat_id, active_symbols):
    parts = text.strip().split()
    cmd = parts[0].lower().split("@")[0]
    if cmd in ("/status", "/start"):
        send(status_text(ex), chat_id)
    elif cmd in ("/pos", "/positions"):
        send(positions_text(ex), chat_id)
    elif cmd == "/risk":
        send(risk_text(ex), chat_id)
    elif cmd == "/daily":
        send(daily_text(ex), chat_id)
    elif cmd == "/stats":
        send(stats_text(), chat_id)
    elif cmd == "/paper":
        send(paper_stats_text(), chat_id)
    elif cmd in ("/blocked", "/حجب"):
        send(_blocked_summary_text(), chat_id)
    elif cmd == "/config":
        send(config_text(), chat_id)
    elif cmd == "/universe":
        send(universe_text(active_symbols), chat_id)
    elif cmd == "/why":
        send(why_text(parts[1] if len(parts) > 1 else None, active_symbols), chat_id)
    elif cmd == "/ai":
        ai_analyze_trades(chat_id)
    elif cmd == "/export":
        send("⏳ جاري تصدير السجل والحالة...", chat_id)
        backup_to_telegram(chat_id)
    elif cmd == "/restore":
        send("أوقف الدخول بـ /pause، وتأكد من عدم وجود مراكز. أرسل الملف في الخاص "
             "مع كتابة <code>/restore confirm</code> في تعليقه.", chat_id)
    elif cmd == "/pause":
        with trade_lock:
            set_meta("paused", True)
        send("⏸️ تم إيقاف فتح صفقات جديدة.", chat_id)
    elif cmd == "/resume":
        with trade_lock:
            set_meta("paused", False)
        send("▶️ تم استئناف البحث.", chat_id)
    elif cmd == "/close":
        if len(parts) < 2:
            send("استخدم: /close BTC", chat_id)
            return
        sym = find_symbol(parts[1], active_symbols)
        if not sym:
            send("عملة غير معروفة.", chat_id)
            return
        with trade_lock:
            with state_lock:
                pos_data = dict(state.get(sym, {}))
            if not pos_data.get("position"):
                send("لا توجد صفقة مفتوحة.", chat_id)
                return
            if close_full(ex, sym):
                finalize_trade(ex, sym, reason_hint="Manual")
            else:
                send("❌ فشل الإغلاق.", chat_id)
    elif cmd == "/closeall":
        if len(parts) < 2 or parts[1].lower() != "confirm":
            send("⚠️ للتأكيد: /closeall confirm", chat_id)
            return
        with trade_lock:
            with state_lock:
                syms = [k for k, v in state.items() if v.get("position")]
            if not syms:
                send("لا توجد صفقات.", chat_id)
                return
            for sym in syms:
                if close_full(ex, sym):
                    finalize_trade(ex, sym, reason_hint="Manual")
                else:
                    send(f"❌ فشل إغلاق <code>{short(sym)}</code>", chat_id)
    elif cmd == "/help":
        send(
            "<b>📋 الأوامر:</b>\n\n"
            "/status - نظرة عامة\n"
            "/why - أسباب الرفض\n"
            "/why BTC - عملة محددة\n"
            "/blocked - العملات المحجوبة\n"
            "/config - الإعدادات\n"
            "/universe - العملات المفحوصة\n"
            "/pos - الصفقات المفتوحة\n"
            "/risk - المخاطر\n"
            "/daily - ملخص اليوم\n"
            "/stats - إحصائيات\n"
            "/paper - إشارات Paper\n"
            "/ai - تحليل AI\n"
            "/export - تصدير السجل\n"
            "/restore - استعادة نسخة\n"
            "/pause - إيقاف\n"
            "/resume - استئناف\n"
            "/close BTC - إغلاق عملة\n"
            "/closeall confirm - إغلاق الكل\n"
            "/help - هذه القائمة",
            chat_id,
        )
