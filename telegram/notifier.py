# notifier.py — طبقة تيليجرام: إرسال غير محجوب عبر queue + صياغة رسائل الدخول والخروج
# لا تعتمد على exchange ولا على state (لتجنب الاستيراد الدائري)
import logging
import queue
import threading
from datetime import datetime

import numpy as np
import requests

from config import TELEGRAM_BOT_TOKEN, AUTHORIZED_CHATS, KSA_TZ, LEVERAGE

log = logging.getLogger("bot")

_send_queue = queue.Queue(maxsize=1000)
STRAT_LABEL = {"pullback": "الارتداد", "breakout": "الاختراق", "adopted": "متبنّاة", "legacy": "قديمة"}


def send(msg, chat_id=None):
    """غير محجوب: يضع الرسالة في queue ويعود فوراً."""
    try:
        _send_queue.put_nowait((msg, chat_id))
    except queue.Full:
        log.warning("[TG QUEUE] full — dropped")


def _send_worker():
    """Thread منفصل يرسل الرسائل من الـ queue."""
    while True:
        try:
            msg, chat_id = _send_queue.get()
            if not TELEGRAM_BOT_TOKEN:
                continue
            targets = [chat_id] if chat_id else list(AUTHORIZED_CHATS)
            for cid in targets:
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                        json={"chat_id": cid, "text": msg, "parse_mode": "HTML"},
                        timeout=10)
                except Exception as e:
                    log.warning(f"[TG SEND] {e}")
        except Exception as e:
            log.error(f"[TG WORKER] {e}")


def fmt(p):
    if p is None or (isinstance(p, float) and np.isnan(p)):
        return "0.00"
    return f"{p:,.4f}" if p >= 1 else f"{p:.6f}"


def short(sym):
    return sym.split(":")[0]


REASONS = {
    "Take Profit": "تحقق الهدف 🎯",
    "Stop Loss": "ضرب وقف الخسارة 🛑",
    "Breakeven Hit": "خروج آمن 🛡️",
    "Trailing Stop": "وقف متحرك 📈",
    "Manual": "إغلاق يدوي 🖐️",
    "Emergency": "إغلاق طارئ 🚨",
    "Manual/External": "إغلاق من المنصة ⚡",
    "Time Exit": "انتهاء مدة القناص ⏱️",
    "Unconfirmed Exit": "سبب الإغلاق غير مؤكد",
    "Estimated Take Profit": "تحقق الهدف (تقديري)",
    "Estimated Stop Loss": "وقف الخسارة (تقديري)",
    "Estimated Trailing Stop": "وقف متحرك (تقديري)",
    "Estimated Breakeven Hit": "تعادل (تقديري)",
}


def msg_entry(sym, side, price, sl, tp, margin, real_order, regime="", strategy="", equity=None, score=0, lev=LEVERAGE, mode="swing"):
    title = "🟢 <b>شراء (LONG)</b>" if side == "LONG" else "🔴 <b>بيع (SHORT)</b>"
    if mode == "sniper":
        title += " 🎯 [قناص]"
    status = "✅ <b>تم الفتح</b>" if real_order else "⚠️ <b>إشارة فقط</b>"
    sl_pct = abs(price - sl) / price * 100
    tp_pct = abs(tp - price) / price * 100
    risk_usd = margin * (sl_pct / 100) * lev
    reward_usd = margin * (tp_pct / 100) * lev
    risk_pct_txt = ""
    if equity and equity > 0:
        pct = risk_usd / equity * 100
        risk_pct_txt = f" = {pct:.2f}% من الرصيد"
    now_str = datetime.now(KSA_TZ).strftime("%I:%M %p")
    regime_txt = f"\n📡 <b>النظام:</b> {regime}" if regime else ""
    if strategy:
        regime_txt += f"\n🧠 <b>الاستراتيجية:</b> {STRAT_LABEL.get(strategy, strategy)}"
    score_txt = f"\n🎯 <b>الدرجة:</b> {score}/100" if score else ""
    return (f"{title}\n{status}{regime_txt}{score_txt}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🪙 <code>{short(sym)}</code> | 💵 <code>{fmt(price)}</code>\n"
            f"⚙️ {lev}x | الهامش: {margin:.2f} USDT\n"
            f"🛑 SL: <code>{fmt(sl)}</code> ({sl_pct:.2f}%) → مخاطرة {risk_usd:.2f}{risk_pct_txt}\n"
            f"🎯 TP: <code>{fmt(tp)}</code> ({tp_pct:.2f}%) → عائد +{reward_usd:.2f}\n"
            f"⏰ {now_str}")


def msg_exit(sym, side, exit_price, entry_price, total_pnl, final_leg_pnl, margin, r_mult,
             reason, strategy="", fees=None, fees_src="", stop_slip_usd=None, stop_slip_r=None):
    pct = (total_pnl / margin * 100) if margin else 0.0
    if total_pnl > 0:
        title = "🟢 <b>ربح</b>"
    elif total_pnl < 0:
        title = "🔴 <b>خسارة</b>"
    else:
        title = "⚪ <b>متعادل</b>"
    extra = ""
    if fees is not None:
        extra += f"🧾 رسوم: {fees:.2f} ({'فعلية' if fees_src == 'actual' else 'تقديرية'})\n"
    if stop_slip_usd is not None and stop_slip_usd >= 0:
        extra += f"⚡ انزلاق وقف: {stop_slip_usd:.2f} USDT ({stop_slip_r:.2f}R)\n"
    return (f"{title}\n━━━━━━━━━━━━━━━\n"
            f"🪙 <code>{short(sym)}</code> ({side})\n"
            f"📥 {fmt(entry_price)} → 📤 {fmt(exit_price)}\n"
            f"📊 هامش: {pct:+.2f}% | R: {r_mult:+.2f}\n"
            f"💰 صافي: <b>{total_pnl:+.2f} USDT</b>\n"
            f"📝 {REASONS.get(reason, reason)} | {STRAT_LABEL.get(strategy, strategy or '—')}\n"
            f"{extra}━━━━━━━━━━━━━━━")


def start_sender():
    """يشغّل thread الإرسال (كان في trading_loop: threading.Thread(target=_send_worker, daemon=True).start())."""
    threading.Thread(target=_send_worker, daemon=True).start()

