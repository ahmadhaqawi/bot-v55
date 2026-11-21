from datetime import datetime

from config import (
    LEVERAGE
)


def short(sym):
    try:
        return sym.split("/")[0]
    except Exception:
        return str(sym)


def fmt(value, digits=2):
    try:
        return f"{float(value):,.{digits}f}"
    except Exception:
        return "-"


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
