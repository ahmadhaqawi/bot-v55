# core/trade_finalize.py — حساب الأرباح وإنهاء الصفقات والإغلاق الطارئ
import time
import logging
from datetime import datetime

from config import (
    BE_FEE_BUFFER, FEE_RATE, FLOATING_FUNDING_BUFFER_PCT,
    GHOST_MAX_AGE_SEC, KSA_TZ, POSITION_SIZE_USDT,
)
from core.state import (
    add_daily_pnl, open_positions_snapshot, register_trade_result,
    reset_sym_state, state, state_lock,
)
from core.journal import journal_append
from exchange import (
    _price_cache, contract_size, get_actual_fees,
    get_current_price, get_exit_price,
)
from protection import cancel_pending, close_full
from telegram.notifier import msg_exit, send, short

log = logging.getLogger("bot")
_last_emergency_msg = {}


def breakeven_price(side, entry):
    return entry * (1 + BE_FEE_BUFFER) if side == "LONG" else entry * (1 - BE_FEE_BUFFER)


def leg_fee_est(entry, exit_price, contracts, cs):
    return (entry + exit_price) * contracts * cs * FEE_RATE


def leg_pnl(side, entry, exit_price, contracts, cs):
    d = 1 if side == "LONG" else -1
    gross = (exit_price - entry) * d * contracts * cs
    return gross - leg_fee_est(entry, exit_price, contracts, cs)


def floating_pnl_cached(ex):
    total = 0.0
    for sym, v in open_positions_snapshot().items():
        c = _price_cache.get(sym)
        price = c[1] if c else v["entry"]
        cs = contract_size(ex, sym)
        total += leg_pnl(v["position"], v["entry"], price, v["contracts"], cs)
        total -= price * v["contracts"] * cs * FLOATING_FUNDING_BUFFER_PCT
    return total


def detect_reason(pos_data, exit_price):
    if exit_price is None:
        return "Unconfirmed Exit"
    side, sl, tp = pos_data["position"], pos_data["sl"], pos_data["tp"]
    d = 1 if side == "LONG" else -1
    near_tp = bool(tp and -0.0005 <= d * (exit_price - tp) / tp <= 0.002)
    near_sl = bool(sl and -0.002 <= d * (exit_price - sl) / sl <= 0.0005)
    if near_tp == near_sl:
        return "Unconfirmed Exit"
    if near_tp:
        return "Estimated Take Profit"
    if pos_data.get("breakeven_hit"):
        be = breakeven_price(side, pos_data["entry"])
        return "Estimated Trailing Stop" if d * (sl - be) > 1e-8 else "Estimated Breakeven Hit"
    return "Estimated Stop Loss"


def finalize_trade(ex, sym, reason_hint=None):
    with state_lock:
        pos_data = dict(state.get(sym, {}))
    if not pos_data.get("position"):
        return
    side, entry = pos_data["position"], pos_data["entry"]
    mode = pos_data.get("mode", "swing")
    d = 1 if side == "LONG" else -1

    age = time.time() - (pos_data.get("opened_ts") or time.time())
    if age < GHOST_MAX_AGE_SEC and (pos_data.get("strategy") or "") != "adopted":
        try:
            fills = ex.fetch_my_trades(sym, since=int(pos_data["opened_ts"] * 1000) - 30000, limit=50)
            if fills is not None and len(fills) == 0:
                log.error(f"[GHOST] {sym}: لا تنفيذات ({age:.0f}ث) — صفقة وهمية، أُهملت")
                reset_sym_state(sym, cooldown=True, mode=mode)
                cancel_pending(ex, sym)
                send(f"⚠️ <b>صفقة وهمية مُهملة</b> <code>{short(sym)}</code>\n"
                     f"لا تنفيذات خلال {age:.0f}ث — لم تُحتسب.")
                return
        except Exception as e:
            log.warning(f"[GHOST CHECK] {sym}: {e}")

    fill_price = get_exit_price(ex, sym, pos_data)
    exit_price = fill_price or get_current_price(ex, sym, 0) or entry
    cs = contract_size(ex, sym)
    contracts = pos_data.get("contracts", 0.0)
    realized = pos_data.get("realized_pnl", 0.0)
    realized_fees_est = pos_data.get("realized_fees_est", 0.0)

    gross_final = (exit_price - entry) * d * contracts * cs
    fee_est_final = leg_fee_est(entry, exit_price, contracts, cs)
    fees_est_total = realized_fees_est + fee_est_final
    fees_actual = get_actual_fees(ex, sym, pos_data)
    use_actual = (fees_actual is not None and fees_est_total > 0 and
                  0.2 * fees_est_total <= fees_actual <= 5 * fees_est_total)
    if use_actual:
        total = realized + realized_fees_est + gross_final - fees_actual
        fees_total, fees_src = fees_actual, "actual"
    else:
        total = realized + gross_final - fee_est_final
        fees_total, fees_src = fees_est_total, "est"
    final_leg = total - realized

    margin = pos_data.get("margin") or POSITION_SIZE_USDT
    reason = reason_hint or detect_reason(pos_data, fill_price)
    init_risk = pos_data.get("initial_risk", 0.0)
    risk_usd = init_risk * pos_data.get("initial_contracts", 0.0) * cs
    r_mult = (total / risk_usd) if risk_usd > 0 else 0.0
    duration = int((time.time() - (pos_data.get("opened_ts") or time.time())) / 60)

    stop_slip_usd = stop_slip_r = None
    if reason in ("Estimated Stop Loss", "Estimated Trailing Stop", "Estimated Breakeven Hit") and pos_data.get("sl"):
        adverse = (pos_data["sl"] - exit_price) if side == "LONG" else (exit_price - pos_data["sl"])
        stop_slip_usd = adverse * contracts * cs
        stop_slip_r = (adverse / init_risk) if init_risk else 0.0
    entry_slip_usd = None
    pe = pos_data.get("planned_entry")
    if pe:
        adverse_e = (entry - pe) if side == "LONG" else (pe - entry)
        entry_slip_usd = adverse_e * pos_data.get("initial_contracts", 0.0) * cs

    factors = pos_data.get("score_factors", {}) or {}
    add_daily_pnl(final_leg)
    register_trade_result(total)
    journal_append({
        "close_time": datetime.now(KSA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": short(sym), "side": side, "entry": entry, "exit": exit_price,
        "initial_contracts": pos_data.get("initial_contracts", 0.0),
        "pnl": round(total, 4), "r_multiple": round(r_mult, 3), "reason": reason,
        "regime": pos_data.get("regime", ""), "duration_min": duration,
        "strategy": pos_data.get("strategy") or "legacy",
        "fees": round(fees_total, 4), "fees_source": fees_src,
        "stop_slip_usd": "" if stop_slip_usd is None else round(stop_slip_usd, 4),
        "stop_slip_r": "" if stop_slip_r is None else round(stop_slip_r, 3),
        "entry_slip_usd": "" if entry_slip_usd is None else round(entry_slip_usd, 4),
        "risk_usd": round(risk_usd, 4), "score": pos_data.get("signal_score", 0),
        "f_trend": factors.get("trend", ""), "f_volume": factors.get("volume", ""),
        "f_adx": factors.get("adx", ""), "f_momentum": factors.get("momentum", ""),
        "f_candle": factors.get("candle", ""), "f_setup": factors.get("setup", ""),
        "f_entry_quality": factors.get("entry_quality", ""),
        "volume_ratio": pos_data.get("volume_ratio", ""), "ker": pos_data.get("ker", ""),
    })
    reset_sym_state(sym, cooldown=True, mode=mode)
    cancel_pending(ex, sym)
    send(msg_exit(sym, side, exit_price, entry, total, final_leg, margin, r_mult, reason,
                  pos_data.get("strategy", ""), fees_total, fees_src, stop_slip_usd, stop_slip_r))


def emergency_close(ex, sym, why):
    now = time.time()
    if now - _last_emergency_msg.get((sym, "start"), 0) > 120:
        _last_emergency_msg[(sym, "start")] = now
        send(f"🚨 <b>إغلاق طارئ</b> <code>{short(sym)}</code>\n{why}")
    if close_full(ex, sym):
        finalize_trade(ex, sym, reason_hint="Emergency")
    else:
        log.error(f"[EMERGENCY] {sym}: فشل الإغلاق الطارئ — سيُعاد")
        if now - _last_emergency_msg.get((sym, "fail"), 0) > 120:
            _last_emergency_msg[(sym, "fail")] = now
            send(f"🆘 فشل الإغلاق الطارئ لـ <code>{short(sym)}</code> — يُعاد. تحقق من المنصة.")
