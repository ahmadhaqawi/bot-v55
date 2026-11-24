# -*- coding: utf-8 -*-
"""main.py — نقطة الدخول والحلقة الرئيسية للبوت (V55 — Binance USDT-M Futures)."""
import os
import sys
import json
import time
import logging
import threading
from datetime import datetime

import numpy as np
import pandas as pd
from flask import Flask

from config import (
    VERSION, KSA_TZ, DATA_DIR, LOCK_FILE, WATCHDOG_SEC, TF_MS,
    BINANCE_API_KEY, BINANCE_SECRET, USE_SANDBOX, ALLOW_LIVE_TRADING,
    FUTURES_SYMBOLS, UNIVERSE_MODE, UNIVERSE_REFRESH_SEC, MAX_UNIVERSE_SYMBOLS,
    INCLUDE_NEW_MARKETS_DAYS, EXCLUDE_BASES, SCAN_INTERVAL,
    ORPHAN_CHECK_SEC, ADOPT_ORPHANS, MISSING_CONFIRMATIONS,
    LEVERAGE, MAX_OPEN_POSITIONS, MAX_SAME_DIRECTION, REENTRY_COOLDOWN_SEC,
    RISK_PCT_PER_TRADE, POSITION_SIZE_USDT, MIN_MARGIN_USDT, MAX_MARGIN_USDT,
    MAX_MARGIN_PCT_EQUITY, ATR_SL_MULT, RR_RATIO, MIN_SL_PCT, MAX_SL_PCT,
    TRAIL_ATR_MULT, BE_TRIGGER_R, FALLBACK_SL_PCT, PARTIAL1_R, PARTIAL1_PCT,
    PARTIAL2_R, PARTIAL2_PCT, BE_FEE_BUFFER, DAILY_LOSS_LIMIT_PCT,
    DAILY_PROFIT_TARGET_PCT, INCLUDE_FLOATING_IN_LOSS_LIMIT, MAX_FUNDING_RATE,
    MAX_ENTRY_SLIPPAGE_PCT, MAX_SPREAD_PCT, USE_BTC_FILTER,
    USE_AI_ENTRY_FILTER, BACKUP_INTERVAL_SEC, AI_MIN_PROB_LONG, AI_MIN_PROB_SHORT,
    PROFILE_NAME, PROFILE_WARNING, ENABLE_SNIPER, SNIPER_ONLY, SNIPER,
    MIN_SIGNAL_SCORE,
    MIN_ENTRY_QUALITY, MIN_ENTRY_QUALITY_PULLBACK,
    ENABLE_PULLBACK, PULLBACK_ALLOWED_REGIMES,
    SHORT_REQUIRE_STRONG_DOWN, SHORT_ALLOWED_REGIMES,
    SHORT_MIN_SCORE, SHORT_REQUIRE_BTC_DOWN,
    WEAK_TREND_MIN_SCORE, WEAK_TREND_RF_RQK_LONG_ONLY,
)
from exchange import (
    _price_cache, _valid_trigger, init_exchange, refresh_prices,
    get_current_price, get_balances, get_equity, contract_size,
    min_amount, get_open_position,
)
from market_data import fetch_data, btc_bias
from protection import (
    has_protection, cancel_pending, place_sl_tp, protect_position,
    replace_protection, close_full, open_position,
)
from risk.sizing import compute_margin_scored
from risk.portfolio import sector_limit_reached
from strategy.scoring import evaluate_signal
from strategy.smc_filters import evaluate_smc_confirmation
from core.state import (
    state, meta, state_lock, trade_lock, _funding_cache, _last_eval,
    _last_spread, _warned_external, new_sym_state, load_state, save_state,
    set_meta, add_daily_pnl, daily_snapshot, open_positions_snapshot,
)
from core.journal import migrate_journal, log_paper_signal
from core.diagnostics import (
    diag, diag_lock, loop_info, universe_info,
    note_eval, note_block, note_opened, set_last_reason,
)
from core.trade_finalize import (
    leg_fee_est, leg_pnl, finalize_trade, emergency_close, floating_pnl_cached,
)
from telegram.notifier import send, short, fmt, msg_entry, start_sender
from telegram.listener import telegram_listener
from backup import backup_to_telegram
import ai.local_ai as ai_state

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

_lock_fh = None
_tp_retry = {}
_bg_started = False
_bg_lock = threading.Lock()

# حدود مرنة لزيادة الفرص مع الحفاظ على أي حد أعلى قادم من config
EFFECTIVE_MAX_OPEN = max(MAX_OPEN_POSITIONS, 8)
EFFECTIVE_MAX_SAME_DIR = max(MAX_SAME_DIRECTION, 6)


# ==========================================
# Flask Health & Diagnostics
# ==========================================
@app.route("/")
@app.route("/healthz")
def health_check():
    return {"status": "ok", "service": f"trading_bot_{VERSION.lower()}"}, 200


@app.route("/diag")
def diag_endpoint():
    with diag_lock:
        d = json.loads(json.dumps(diag))
    last = loop_info["last_cycle_ts"]
    return {
        "version": VERSION, "profile": PROFILE_NAME,
        "paused": bool(meta.get("paused")),
        "loop_age_sec": round(time.time() - last, 1) if last else None,
        "cycles": loop_info["cycles"],
        "last_cycle_duration": round(loop_info["last_duration"], 1),
        "last_error": loop_info["last_error"],
        "today": d,
    }, 200


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)


def acquire_single_instance():
    global _lock_fh
    try:
        import fcntl
        _lock_fh = open(LOCK_FILE, "w")
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except ImportError:
        return True
    except OSError:
        return False


# ==========================================
# إعدادات القناص والفلاتر
# ==========================================
def _extract_entry_quality(signal):
    factors = signal.get("score_factors") or {}
    for k in ("f_entry_quality", "entry_quality"):
        if signal.get(k) is not None:
            try:
                return int(signal.get(k))
            except (TypeError, ValueError):
                pass
        if factors.get(k) is not None:
            try:
                return int(factors.get(k))
            except (TypeError, ValueError):
                pass
    return 0


def post30_signal_filters_ok(signal, bias="neutral"):
    side = str(signal.get("side", "")).upper()
    strategy = str(signal.get("strategy", "")).lower()
    regime = str(signal.get("regime", "")).lower()
    score = float(signal.get("score", 0.0))
    entry_quality = _extract_entry_quality(signal)
    btc_dir = str(bias or "").lower()

    if strategy == "pullback":
        if not ENABLE_PULLBACK:
            return False, "pullback_disabled"
        if regime not in PULLBACK_ALLOWED_REGIMES:
            return False, "pullback_regime"
        if entry_quality < MIN_ENTRY_QUALITY_PULLBACK:
            return False, "pullback_low_quality"

    if entry_quality < MIN_ENTRY_QUALITY:
        return False, "low_entry_quality"

    if side == "SHORT":
        if SHORT_REQUIRE_STRONG_DOWN and regime not in SHORT_ALLOWED_REGIMES:
            return False, "short_not_strong_down"
        if score < SHORT_MIN_SCORE:
            return False, "short_score_low"
        if SHORT_REQUIRE_BTC_DOWN and btc_dir != "down":
            return False, "short_btc_not_down"

    if regime == "weak_trend":
        if score < WEAK_TREND_MIN_SCORE:
            return False, "weak_trend_score_low"
        if WEAK_TREND_RF_RQK_LONG_ONLY and not (strategy == "rf_rqk" and side == "LONG"):
            return False, "weak_trend_not_rf_rqk_long"

    return True, ""


def sniper_filters_ok(df_entry, current_price, signal_price, ker, vol_ratio):
    if ker is None or ker < SNIPER["min_ker"]:
        return False, "low_ker_sniper"
    if vol_ratio < SNIPER["min_vol_mult"]:
        return False, "low_volume_sniper"
    try:
        ema21 = float(df_entry["ema21"].iloc[-2])
        if abs(current_price - ema21) / current_price > SNIPER["near_ema_pct"]:
            return False, "far_from_ema_sniper"
    except Exception:
        return False, "ema_error_sniper"
    if abs(current_price - signal_price) / signal_price > SNIPER["max_entry_slip"]:
        return False, "slippage_sniper"
    return True, ""


def get_trade_params(mode="swing"):
    if mode == "sniper":
        return {
            "partial1_r": SNIPER["p1_r"], "partial1_pct": SNIPER["p1_pct"],
            "partial2_r": SNIPER["p2_r"], "partial2_pct": SNIPER["p2_pct"],
            "trail_atr": SNIPER["trail_atr"], "be_r": SNIPER["be_r"],
            "max_hold_sec": SNIPER["max_hold_minutes"] * 60,
            "atr_sl_mult": SNIPER["atr_sl_mult"], "rr": SNIPER["rr"],
            "min_sl": SNIPER["min_sl"], "max_sl": SNIPER["max_sl"],
            "leverage": SNIPER["leverage"], "risk_pct": SNIPER["risk_pct"],
        }
    return {
        "partial1_r": PARTIAL1_R, "partial1_pct": PARTIAL1_PCT,
        "partial2_r": PARTIAL2_R, "partial2_pct": PARTIAL2_PCT,
        "trail_atr": TRAIL_ATR_MULT, "be_r": BE_TRIGGER_R,
        "max_hold_sec": 36 * 3600,
        "atr_sl_mult": ATR_SL_MULT, "rr": RR_RATIO,
        "min_sl": MIN_SL_PCT, "max_sl": MAX_SL_PCT,
        "leverage": LEVERAGE, "risk_pct": RISK_PCT_PER_TRADE,
    }


def market_filters_ok(ex, sym, side):
    now = time.time()
    cached = _price_cache.get(sym)
    last = bid = ask = None

    if cached and (now - cached[0]) <= 15 and _valid_trigger(cached[1]):
        last, bid, ask = cached[1], cached[2], cached[3]
    else:
        try:
            t = ex.fetch_ticker(sym)
            raw_last = t.get("last")
            if not _valid_trigger(raw_last):
                return False, "ticker_unavailable"
            last = float(raw_last)
            bid = float(t["bid"]) if _valid_trigger(t.get("bid")) else None
            ask = float(t["ask"]) if _valid_trigger(t.get("ask")) else None
            _price_cache[sym] = (now, last, bid, ask)
        except Exception as e:
            log.warning(f"[FILTER TICKER] {sym}: {e}")
            return False, "ticker_unavailable"

    if bid is not None and ask is not None and ask >= bid:
        spread = (ask - bid) / last
        _last_spread[sym] = (now, spread)
        if spread > MAX_SPREAD_PCT:
            return False, "spread_high"
    elif USE_SANDBOX:
        _last_spread[sym] = (now, 0.0)
    else:
        return False, "ticker_unavailable"

    f_cached = _funding_cache.get(sym)
    if f_cached and (now - f_cached[0]) < 900:
        fr = f_cached[1]
    else:
        try:
            fr = ex.fetch_funding_rate(sym).get("fundingRate")
            _funding_cache[sym] = (now, fr)
        except Exception as e:
            log.warning(f"[FILTER FUNDING] {sym}: {e}")
            fr = None

    if fr is not None:
        if side == "LONG" and fr > MAX_FUNDING_RATE:
            return False, "funding_high"
        if side == "SHORT" and fr < -MAX_FUNDING_RATE:
            return False, "funding_negative"
    return True, ""


# ==========================================
# إدارة المراكز المفتوحة
# ==========================================
def breakeven_price(side, entry):
    return entry * (1 + BE_FEE_BUFFER) if side == "LONG" else entry * (1 - BE_FEE_BUFFER)


def move_sl(ex, sym, st, new_sl, title, notify=True):
    side = st["position"]
    close_side = "sell" if side == "LONG" else "buy"
    if not replace_protection(ex, sym, close_side, st["contracts"], new_sl, None):
        return False
    with state_lock:
        state[sym]["sl"] = new_sl
        state[sym]["breakeven_hit"] = True
        prev_notified = state[sym].get("last_notified_sl")
        risk = state[sym].get("initial_risk") or 0
        big_move = (prev_notified is None or risk <= 0 or abs(new_sl - prev_notified) >= 0.3 * risk)
        if notify and big_move:
            state[sym]["last_notified_sl"] = new_sl
    save_state()
    if notify and big_move:
        send(f"{title}\n🪙 <code>{short(sym)}</code>\n🛑 الوقف الجديد: <code>{fmt(new_sl)}</code>")
    return True


def do_partial(ex, sym, st, which):
    side = st["position"]
    close_side = "sell" if side == "LONG" else "buy"
    params = get_trade_params(st.get("mode", "swing"))
    contracts = st["contracts"]
    raw_amt = st["initial_contracts"] * params["partial1_pct"] if which == 1 else contracts * params["partial2_pct"]
    amt = float(ex.amount_to_precision(sym, raw_amt))
    mn = min_amount(ex, sym)
    if amt < mn or (contracts - amt) < mn:
        with state_lock:
            state[sym][f"partial_{which}_taken"] = True
        save_state()
        return False
    pending = {"which": which, "before": contracts, "id": None, "price": None, "ts": time.time()}
    with state_lock:
        state[sym]["partial_order_pending"] = pending
    save_state()
    try:
        order = ex.create_order(sym, "market", close_side, amt, None, {"reduceOnly": True})
        with state_lock:
            pending.update(id=order.get("id"), price=order.get("average") or order.get("price"))
        save_state()
    except Exception as e:
        log.error(f"[PARTIAL ORDER] {sym}: {e}")
        with state_lock:
            state[sym]["partial_order_pending"] = None
        save_state()
        send(f"⚠️ تعذر إرسال طلب الإغلاق الجزئي لـ <code>{short(sym)}</code>.")
        return False
    time.sleep(0.8)
    try:
        after = get_open_position(ex, sym)
        if after and after["side"] == side:
            reconcile_partial(ex, sym, st, pending, after["contracts"])
    except Exception as e:
        log.warning(f"[PARTIAL POS FETCH] {sym}: {e}")
    return True


def reconcile_partial(ex, sym, st, pending, remaining):
    which, contracts = pending["which"], pending["before"]
    actual_closed = contracts - remaining
    if actual_closed <= 1e-10 or remaining <= 0:
        if time.time() - pending.get("ts", 0) > 30:
            with state_lock:
                state[sym]["partial_order_pending"] = None
            save_state()
        return False
    side, entry = st["position"], st["entry"]
    fill_ts = time.time()
    exit_price = pending.get("price")
    try:
        if not exit_price and pending.get("id"):
            fetched = ex.fetch_order(pending["id"], sym)
            exit_price = fetched.get("average") or fetched.get("price")
    except Exception as e:
        log.warning(f"[PARTIAL FILL PRICE] {sym}: {e}")
    exit_price = float(exit_price or get_current_price(ex, sym, 0) or entry)
    cs_leg = contract_size(ex, sym)
    pnl = leg_pnl(side, entry, exit_price, actual_closed, cs_leg)
    fee_est_leg = leg_fee_est(entry, exit_price, actual_closed, cs_leg)
    new_sl = st["sl"]
    if which == 1:
        be = breakeven_price(side, entry)
        new_sl = max(be, new_sl) if side == "LONG" else min(be, new_sl)
    with state_lock:
        s = state[sym]
        s["contracts"] = remaining
        s[f"partial_{which}_taken"] = True
        s["realized_pnl"] = s.get("realized_pnl", 0.0) + pnl
        s["realized_fees_est"] = s.get("realized_fees_est", 0.0) + fee_est_leg
        s["leg_ts"] = fill_ts
        s["pending_protection_sl"] = new_sl
        s["partial_order_pending"] = None
    add_daily_pnl(pnl)
    send(f"💰 <b>إغلاق جزئي {which}</b> | <code>{short(sym)}</code>\n"
         f"ربح الجزء: {pnl:+.2f} USDT. جارٍ التحقق من حماية المتبقي.")
    repair_protection(ex, sym, dict(state[sym]), get_current_price(ex, sym))
    return True


def repair_protection(ex, sym, st, price):
    side = st["position"]
    close_side = "sell" if side == "LONG" else "buy"
    contracts = st["contracts"]
    has_sl, has_tp = has_protection(ex, sym, close_side, contracts)
    pending_sl = st.get("pending_protection_sl")
    if has_sl is None and pending_sl is None:
        return False
    if not has_sl or pending_sl is not None:
        desired = pending_sl or st.get("sl")
        breached = price and desired and (price <= desired if side == "LONG" else price >= desired)
        if breached or not desired:
            if has_sl is not True or pending_sl is not None:
                emergency_close(ex, sym, "تجاوز الوقف أو تعذر تحديده")
            return False
        if replace_protection(ex, sym, close_side, contracts, desired, st.get("tp")):
            with state_lock:
                state[sym]["sl"] = desired
                state[sym]["pending_protection_sl"] = None
                entry = st["entry"]
                state[sym]["breakeven_hit"] = (desired >= entry if side == "LONG" else desired <= entry)
            save_state()
        else:
            has_sl, _ = has_protection(ex, sym, close_side, contracts)
            if has_sl is not True:
                emergency_close(ex, sym, "تعذر تأكيد وقف يغطي الكمية المتبقية")
        return False

    tp = st.get("tp")
    if tp and has_tp is False and price and time.monotonic() >= _tp_retry.get(sym, 0):
        _tp_retry[sym] = time.monotonic() + 30
        if (price >= tp if side == "LONG" else price <= tp):
            fresh = get_current_price(ex, sym, 0)
            if not fresh:
                return False
            if (fresh >= tp if side == "LONG" else fresh <= tp):
                if close_full(ex, sym):
                    finalize_trade(ex, sym, reason_hint="Take Profit")
                return False
        try:
            place_sl_tp(ex, sym, close_side, contracts, tp, False)
        except Exception as e:
            log.warning("[TP REPAIR] %s: %s", sym, e)
    return True


def manage_position(ex, sym, st):
    side, entry = st["position"], st["entry"]
    d = 1 if side == "LONG" else -1
    price = get_current_price(ex, sym)
    pending = st.get("partial_order_pending")
    if pending and reconcile_partial(ex, sym, st, pending, st["contracts"]):
        return
    if not repair_protection(ex, sym, st, price) or not price:
        return

    mode = st.get("mode", "swing")
    params = get_trade_params(mode)
    if mode == "sniper" and st.get("opened_ts") and (time.time() - st["opened_ts"]) >= params["max_hold_sec"]:
        if close_full(ex, sym):
            finalize_trade(ex, sym, reason_hint="Time Exit")
        return

    risk = st.get("initial_risk") or abs(entry - (st.get("initial_sl") or st["sl"]))
    if risk <= 0:
        return
    r_now = (price - entry) * d / risk

    if not pending and not st.get("partial_1_taken") and r_now >= params["partial1_r"]:
        if do_partial(ex, sym, st, 1):
            return
    elif not pending and st.get("partial_1_taken") and not st.get("partial_2_taken") and r_now >= params["partial2_r"]:
        if do_partial(ex, sym, st, 2):
            return

    if not st.get("breakeven_hit"):
        if r_now >= params["be_r"]:
            move_sl(ex, sym, st, breakeven_price(side, entry), "🛡️ تأمين الصفقة (Breakeven)")
        return

    df = fetch_data(ex, sym, "5m" if mode == "sniper" else "15m")
    if len(df) < 60:
        return
    atr_val = df.iloc[-2].get("atr")
    if atr_val is None or pd.isna(atr_val):
        return
    mult = params["trail_atr"]
    if st.get("partial_2_taken"):
        mult *= 0.82
    elif st.get("partial_1_taken"):
        mult *= 0.90
    be = breakeven_price(side, entry)
    sl = st["sl"]
    if side == "LONG":
        new_sl = max(price - mult * float(atr_val), be)
        if new_sl > sl * 1.001 and new_sl < price:
            move_sl(ex, sym, st, new_sl, "📈 تحريك الوقف المتحرك")
    else:
        new_sl = min(price + mult * float(atr_val), be)
        if new_sl < sl * 0.999 and new_sl > price:
            move_sl(ex, sym, st, new_sl, "📈 تحريك الوقف المتحرك")


# ==========================================
# السماح بالدخول ومعالجة الرموز
# ==========================================
def entries_allowed(ex):
    if meta.get("paused"):
        return False, "paused"
    snap = daily_snapshot()
    equity = get_equity(ex)
    if equity <= 0:
        return False, "equity_unavailable"
    loss_now = snap["value"]
    if INCLUDE_FLOATING_IN_LOSS_LIMIT:
        loss_now += min(floating_pnl_cached(ex), 0.0)
    if loss_now <= -equity * DAILY_LOSS_LIMIT_PCT / 100.0:
        if meta.get("loss_alert") != snap["date"]:
            set_meta("loss_alert", snap["date"])
            send("⛔ <b>وصلنا حد الخسارة اليومي</b>\nتم إيقاف الصفقات الجديدة")
        return False, "daily_loss_limit"
    if DAILY_PROFIT_TARGET_PCT > 0 and snap["value"] >= equity * DAILY_PROFIT_TARGET_PCT / 100.0:
        if meta.get("profit_alert") != snap["date"]:
            set_meta("profit_alert", snap["date"])
            send("✅ <b>تحقق الهدف اليومي للربح</b>\nتم إيقاف الدخول لحماية الأرباح")
        return False, "daily_profit_target"
    return True, ""


def execute_ranked_entry(ex, candidate):
    sym = candidate["sym"]
    signal = candidate["signal"]
    mode = candidate["mode"]
    pos_data = candidate["pos_data"]

    with trade_lock:
        opens = open_positions_snapshot()
        with state_lock:
            current = state.get(sym, {})
        side = signal["side"]
        if (current.get("position") or current.get("last_exit_ts") != pos_data.get("last_exit_ts")
                or len(opens) >= EFFECTIVE_MAX_OPEN
                or sum(v.get("position") == side for v in opens.values()) >= EFFECTIVE_MAX_SAME_DIR
                or (mode == "sniper" and sum(v.get("mode") == "sniper" for v in opens.values()) >= SNIPER["max_open"])
                or sector_limit_reached(opens, sym, side)):
            return
        if meta.get("paused") or get_open_position(ex, sym) is not None:
            return
        equity_now, free_margin = get_balances(ex, force=True)
        ok, gate = entries_allowed(ex)
        if not ok:
            note_block(sym, gate)
            return

        max_slip = SNIPER["max_entry_slip"] if mode == "sniper" else max(MAX_ENTRY_SLIPPAGE_PCT, 0.18 * signal["sl_pct"])
        t_params = get_trade_params(mode)
        sl_pct = max(t_params["min_sl"], min(t_params["max_sl"], signal["sl_pct"]))
        tp_rr, lev_eff, risk_eff = t_params["rr"], t_params["leverage"], t_params["risk_pct"]

        margin = compute_margin_scored(
            equity=equity_now, sl_pct=sl_pct, signal_score=signal.get("score", 0),
            free_margin=free_margin, risk_pct=risk_eff, leverage=lev_eff,
            ai_prob=None if mode == "sniper" else candidate.get("ai_prob"),
            ai_active=ai_state.AI_IS_ACTIVE,
            min_margin_usdt=MIN_MARGIN_USDT, max_margin_usdt=MAX_MARGIN_USDT,
            max_margin_pct_equity=MAX_MARGIN_PCT_EQUITY,
        )
        if margin <= 0:
            note_block(sym, "margin_too_small")
            log_paper_signal(sym, signal, "margin_too_small")
            return
        actual_price = get_current_price(ex, sym, 0)
        if not actual_price or abs(actual_price - signal["price"]) / signal["price"] > max_slip:
            reason = "slippage" if actual_price else "ticker_unavailable"
            note_block(sym, reason)
            log_paper_signal(sym, signal, reason)
            return
        d = 1 if side == "LONG" else -1
        sl, tp = actual_price * (1 - d * sl_pct), actual_price * (1 + d * sl_pct * tp_rr)
        success, info, entry, contracts = open_position(ex, sym, side, actual_price, sl, tp, margin, leverage=lev_eff)
        now = time.time()
        if success:
            note_opened()
            with state_lock:
                state[sym].update({
                    "position": side, "entry": entry, "sl": sl, "tp": tp,
                    "initial_sl": sl, "initial_risk": abs(entry - sl),
                    "contracts": contracts, "initial_contracts": contracts,
                    "margin": margin, "opened_ts": now, "leg_ts": now,
                    "realized_pnl": 0.0, "realized_fees_est": 0.0,
                    "planned_entry": actual_price, "last_notified_sl": None,
                    "breakeven_hit": False, "partial_1_taken": False, "partial_2_taken": False,
                    "regime": signal.get("regime", ""), "strategy": signal.get("strategy", ""),
                    "mode": mode, "signal_score": signal.get("score", 0),
                    "score_factors": signal.get("score_factors", {}),
                    "volume_ratio": signal.get("volume_ratio", 0), "ker": signal.get("ker"),
                })
        else:
            note_block(sym, "open_failed")
            with state_lock:
                state[sym]["last_exit_ts"] = now
                state[sym]["last_exit_mode"] = mode
        save_state()
    caption = msg_entry(sym, side, entry, sl, tp, margin, success,
                        signal.get("regime", ""), signal.get("strategy", ""),
                        equity_now, signal.get("score", 0), lev=lev_eff, mode=mode)
    if not success:
        caption += f"\n❌ {info}"
    send(caption)


def process_symbol(ex, sym, collected_candidates=None):
    try:
        with trade_lock:
            with state_lock:
                st = state.setdefault(sym, new_sym_state())
                pos_data = dict(st)

            if pos_data.get("position") is not None:
                exch_pos = get_open_position(ex, sym)
                if exch_pos is None:
                    with state_lock:
                        state[sym]["missing_checks"] = state[sym].get("missing_checks", 0) + 1
                        missing = state[sym]["missing_checks"]
                    if missing < MISSING_CONFIRMATIONS:
                        return
                    finalize_trade(ex, sym)
                    return
                if exch_pos["side"] != pos_data["position"]:
                    log.error("[POSITION SIDE MISMATCH] %s: verify in Binance", sym)
                    return
                with state_lock:
                    state[sym]["missing_checks"] = 0
                    state[sym]["contracts"] = exch_pos["contracts"]
                    cur = dict(state[sym])
                manage_position(ex, sym, cur)
                return

        last_exit_mode = pos_data.get("last_exit_mode") or "swing"
        effective_cooldown = SNIPER["cooldown"] if last_exit_mode == "sniper" else REENTRY_COOLDOWN_SEC
        if time.time() - pos_data.get("last_exit_ts", 0) < effective_cooldown:
            set_last_reason(sym, "cooldown")
            return
        ok, gate = entries_allowed(ex)
        if not ok:
            set_last_reason(sym, gate)
            return
        if len(open_positions_snapshot()) >= EFFECTIVE_MAX_OPEN:
            set_last_reason(sym, "max_positions")
            return

        df_15m = fetch_data(ex, sym, "15m")
        if len(df_15m) < 70:
            set_last_reason(sym, "no_data")
            return

        swing_ts = int(df_15m["timestamp"].iloc[-2])
        want_swing = not SNIPER_ONLY and _last_eval.get((sym, "swing")) != swing_ts
        expected_5m = (int(time.time() * 1000 // TF_MS["5m"]) - 1) * TF_MS["5m"]
        want_sniper = ENABLE_SNIPER and _last_eval.get((sym, "sniper")) != expected_5m
        if not want_swing and not want_sniper:
            return

        df_1h = fetch_data(ex, sym, "1h")
        if len(df_1h) < 60:
            set_last_reason(sym, "no_data")
            return
        df_5m = fetch_data(ex, sym, "5m") if want_sniper else pd.DataFrame()

        signal, reasons, mode = None, {}, "swing"
        if want_sniper and len(df_5m) >= 70:
            try:
                _last_eval[(sym, "sniper")] = int(df_5m["timestamp"].iloc[-2])
                signal_s, reasons_s = evaluate_signal(df_5m, df_15m, df_1h, timeframe="5m")
                reasons = reasons_s or {"common": "score_low"}
                if signal_s and signal_s.get("score", 0) >= SNIPER["min_score"]:
                    cur_px = get_current_price(ex, sym) or signal_s["price"]
                    ok_s, r_s = sniper_filters_ok(df_5m, cur_px, signal_s["price"], signal_s.get("ker"), signal_s.get("volume_ratio", 0))
                    if ok_s:
                        sniper_open = sum(1 for v in open_positions_snapshot().values() if v.get("mode") == "sniper")
                        if sniper_open < SNIPER["max_open"]:
                            signal, reasons, mode = signal_s, reasons_s, "sniper"
                    else:
                        reasons = {"common": r_s}
            except Exception as e:
                log.warning(f"[SNIPER ERROR] {sym}: {e}")

        if signal is None and want_swing:
            df_4h = fetch_data(ex, sym, "4h")
            if len(df_4h) >= 60:
                _last_eval[(sym, "swing")] = swing_ts
                signal, reasons = evaluate_signal(df_15m, df_1h, df_4h, timeframe="15m")
                mode = "swing"

        if not signal and not reasons:
            set_last_reason(sym, "no_data")
            return

        note_eval(sym, signal, reasons)
        if not signal:
            return

        tf_ms = TF_MS["5m" if mode == "sniper" else "15m"]
        max_age_sec = SNIPER["max_signal_age_sec"] if mode == "sniper" else 240
        if (time.time() * 1000 - (signal.get("candle_ts", 0) + tf_ms)) > max_age_sec * 1000:
            note_block(sym, "signal_expired")
            return

        side = signal["side"]
        opens = open_positions_snapshot()
        if sum(1 for v in opens.values() if v.get("position") == side) >= EFFECTIVE_MAX_SAME_DIR:
            note_block(sym, "max_same_direction")
            log_paper_signal(sym, signal, "max_same_direction")
            return
        if sector_limit_reached(opens, sym, side):
            note_block(sym, "sector_limit")
            log_paper_signal(sym, signal, "sector_limit")
            return
        bias = btc_bias(ex) if USE_BTC_FILTER else "neutral"
        if USE_BTC_FILTER:
            if (side == "LONG" and bias == "down") or (side == "SHORT" and bias == "up"):
                note_block(sym, "btc_filter")
                log_paper_signal(sym, signal, "btc_filter")
                return

        p30_ok, p30_reason = post30_signal_filters_ok(signal, bias=bias)
        if not p30_ok:
            note_block(sym, p30_reason)
            log_paper_signal(sym, signal, p30_reason)
            return

        smc_df = df_5m if (mode == "sniper" and len(df_5m) >= 35) else df_15m
        smc_res = evaluate_smc_confirmation(smc_df, side=side)
        if not smc_res.get("allowed", True):
            smc_reason = smc_res.get("reason", "no_smc_sweep_or_fvg")
            note_block(sym, smc_reason)
            log_paper_signal(sym, signal, smc_reason)
            return

        smc_bonus = int(smc_res.get("score_bonus", 0))
        if smc_bonus:
            signal["score"] = min(100.0, float(signal.get("score", 0.0)) + smc_bonus)

        m_ok, m_reason = market_filters_ok(ex, sym, side)
        if not m_ok:
            note_block(sym, m_reason)
            log_paper_signal(sym, signal, m_reason)
            return

        ai_prob = None
        if ai_state.AI_IS_ACTIVE:
            try:
                ai_prob = ai_state.predict_signal(signal)
                thr = AI_MIN_PROB_LONG if side == "LONG" else AI_MIN_PROB_SHORT
                if signal.get("score", 0) >= 86:
                    thr = max(0.35, thr - 0.05)
                if mode != "sniper" and USE_AI_ENTRY_FILTER and ai_prob is not None and ai_prob < thr:
                    note_block(sym, "ai_rejected")
                    log_paper_signal(sym, signal, f"ai_rejected_{ai_prob:.2f}")
                    return
            except Exception as e:
                log.warning(f"[AI ERROR] {e}")
                ai_prob = None

        candidate_item = {
            "sym": sym,
            "signal": signal,
            "mode": mode,
            "pos_data": pos_data,
            "ai_prob": ai_prob,
            "score": float(signal.get("score", 0)),
        }
        if collected_candidates is not None:
            collected_candidates.append(candidate_item)
        else:
            execute_ranked_entry(ex, candidate_item)
    except Exception as e:
        loop_info["last_error"] = f"{sym}: {str(e)[:150]}"
        log.error(f"[ERROR] {sym}: {e}")


# ==========================================
# مزامنة المراكز واكتشاف السوق (Binance)
# ==========================================
def adopt_sl_pct(ex, sym):
    try:
        df = fetch_data(ex, sym, "15m")
        if len(df) >= 60:
            a, c = float(df.iloc[-2]["atr"]), float(df.iloc[-2]["close"])
            if c > 0 and not np.isnan(a):
                return max(MIN_SL_PCT, min(MAX_SL_PCT, ATR_SL_MULT * a / c))
    except Exception:
        pass
    return FALLBACK_SL_PCT


def sync_positions(ex, active_symbols):
    with trade_lock:
        try:
            exch = {p["symbol"]: p for p in ex.fetch_positions() if abs(float(p.get("contracts") or 0)) > 0 and p.get("symbol")}
        except Exception as e:
            log.error(f"[SYNC] {e}")
            return
        for sym in exch:
            if sym not in active_symbols:
                active_symbols.append(sym)
        for sym in list(active_symbols):
            with state_lock:
                st = dict(state.get(sym, new_sym_state()))
            if st.get("position") and sym not in exch:
                finalize_trade(ex, sym)
            elif st.get("position") and sym in exch:
                if st["position"].lower() != exch[sym].get("side") or (exch[sym].get("info") or {}).get("positionSide") in ("LONG", "SHORT"):
                    log.error("[SYNC] %s: side/mode mismatch (requires One-Way)", sym)
                    continue
                contracts = abs(float(exch[sym].get("contracts")))
                with state_lock:
                    state[sym]["contracts"] = contracts
                st["contracts"] = contracts
                repair_protection(ex, sym, st, get_current_price(ex, sym))
            elif not st.get("position") and sym in exch and ADOPT_ORPHANS:
                p = exch[sym]
                if (p.get("info") or {}).get("positionSide") in ("LONG", "SHORT") or p.get("side") not in ("long", "short"):
                    continue
                side = "LONG" if p.get("side") == "long" else "SHORT"
                entry, contracts = float(p.get("entryPrice") or 0), abs(float(p.get("contracts")))
                sl_pct = adopt_sl_pct(ex, sym)
                sl = entry * (1 - sl_pct) if side == "LONG" else entry * (1 + sl_pct)
                tp = entry * (1 + sl_pct * RR_RATIO) if side == "LONG" else entry * (1 - sl_pct * RR_RATIO)
                close_side = "sell" if side == "LONG" else "buy"
                cancel_pending(ex, sym)
                if protect_position(ex, sym, close_side, contracts, sl, tp)[0]:
                    now = time.time()
                    with state_lock:
                        state[sym] = new_sym_state()
                        state[sym].update({
                            "position": side, "entry": entry, "sl": sl, "tp": tp,
                            "initial_sl": sl, "initial_risk": abs(entry - sl),
                            "contracts": contracts, "initial_contracts": contracts,
                            "margin": float(p.get("initialMargin") or POSITION_SIZE_USDT),
                            "opened_ts": now, "leg_ts": now, "regime": "adopted", "strategy": "adopted",
                        })
                    send(f"♻️ تم تبني مركز <code>{short(sym)}</code> ({side}) — SL {sl_pct * 100:.2f}% (ATR)")
        save_state()


def adopt_orphans(ex, active_symbols):
    with trade_lock:
        try:
            exch = {p["symbol"]: p for p in ex.fetch_positions() if abs(float(p.get("contracts") or 0)) > 0 and p.get("symbol")}
        except Exception as e:
            log.warning(f"[ORPHAN] {e}")
            return
        for sym, p in exch.items():
            if (p.get("info") or {}).get("positionSide") in ("LONG", "SHORT") or p.get("side") not in ("long", "short"):
                continue
            if sym not in active_symbols:
                active_symbols.append(sym)
            with state_lock:
                st = state.get(sym) or new_sym_state()
            if st.get("position"):
                continue
            side = "LONG" if p.get("side") == "long" else "SHORT"
            if not ADOPT_ORPHANS:
                if sym not in _warned_external:
                    _warned_external.add(sym)
                    send(f"⚠️ مركز غير مُدار على <code>{short(sym)}</code> ({side})")
                continue
            entry, contracts = float(p.get("entryPrice") or 0), abs(float(p.get("contracts")))
            sl_pct = adopt_sl_pct(ex, sym)
            sl = entry * (1 - sl_pct) if side == "LONG" else entry * (1 + sl_pct)
            tp = entry * (1 + sl_pct * RR_RATIO) if side == "LONG" else entry * (1 - sl_pct * RR_RATIO)
            cur = get_current_price(ex, sym)
            if cur and ((side == "LONG" and cur <= sl) or (side == "SHORT" and cur >= sl)):
                close_full(ex, sym)
                continue
            close_side = "sell" if side == "LONG" else "buy"
            cancel_pending(ex, sym)
            if protect_position(ex, sym, close_side, contracts, sl, tp)[0]:
                now = time.time()
                with state_lock:
                    state[sym] = new_sym_state()
                    state[sym].update({
                        "position": side, "entry": entry, "sl": sl, "tp": tp,
                        "initial_sl": sl, "initial_risk": abs(entry - sl),
                        "contracts": contracts, "initial_contracts": contracts,
                        "margin": float(p.get("initialMargin") or POSITION_SIZE_USDT),
                        "opened_ts": now, "leg_ts": now, "regime": "adopted", "strategy": "adopted",
                    })
                save_state()
                send(f"♻️ تم تبني مركز يتيم <code>{short(sym)}</code> ({side}) — SL {sl_pct * 100:.2f}% (ATR)")
            else:
                close_full(ex, sym)


def discover_universe(ex):
    core = [x for x in FUTURES_SYMBOLS if x in ex.markets]
    if UNIVERSE_MODE == "core":
        universe_info.update({"mode": "core", "size": len(core), "ts": time.time()})
        return core
    candidates, excluded = [], {"قائمة سوداء": []}
    for sym, m in ex.markets.items():
        if not m.get("active", True) or not m.get("swap") or m.get("quote") != "USDT" or m.get("settle") not in (None, "USDT"):
            continue
        base = (m.get("base") or sym.split("/")[0]).upper()
        if base in EXCLUDE_BASES:
            excluded["قائمة سوداء"].append(base)
            continue
        candidates.append((sym, m))
    volumes = {}
    try:
        for sym, t in ex.fetch_tickers([x[0] for x in candidates]).items():
            qv = t.get("quoteVolume") or (float(t.get("baseVolume") or 0) * float(t.get("last") or 0))
            volumes[sym] = float(qv or 0)
    except Exception as e:
        log.warning(f"[UNIVERSE] {e}")
        return core
    ranked = sorted(candidates, key=lambda x: volumes.get(x[0], 0), reverse=True)
    result = [sym for sym, _ in ranked if volumes.get(sym, 0) > 0][:MAX_UNIVERSE_SYMBOLS] or core
    universe_info.update({"mode": "dynamic", "size": len(result), "ts": time.time(),
                          "excluded": {k: len(v) for k, v in excluded.items()}})
    return result


def watchdog():
    while True:
        time.sleep(60)
        last = loop_info["last_cycle_ts"]
        if not last:
            continue
        age = time.time() - last
        if age > WATCHDOG_SEC and not loop_info["alerted"]:
            loop_info["alerted"] = True
            send(f"🚨 <b>تحذير:</b> حلقة الفحص متوقفة منذ {int(age)}ث.\nآخر خطأ: {loop_info['last_error'] or '—'}")
        elif age <= WATCHDOG_SEC and loop_info["alerted"]:
            loop_info["alerted"] = False
            send("✅ عادت حلقة الفحص للعمل.")


def maybe_send_daily_report(ex):
    now = datetime.now(KSA_TZ)
    today = str(now.date())
    if now.hour == 23 and now.minute >= 55 and meta.get("last_report") != today:
        snap = daily_snapshot()
        wr = (snap["wins"] / snap["trades"] * 100) if snap["trades"] else 0
        with diag_lock:
            ev, sg, op = diag["evaluated"], diag["signals"], diag["opened"]
        send(f"📋 <b>التقرير اليومي {VERSION}</b> — {today}\n━━━━━━━━━━━━━━━\n"
             f"💰 النتيجة: {snap['value']:+.2f} USDT\n"
             f"🔢 الصفقات: {snap['trades']} | رابحة: {snap['wins']} ({wr:.0f}%)\n"
             f"🔎 تقييمات {ev} | إشارات {sg} | فُتحت {op}\n"
             f"💼 الرصيد: {get_equity(ex, force=True):,.2f} USDT")
        set_meta("last_report", today)


def maybe_send_heartbeat(ex):
    now = datetime.now(KSA_TZ)
    cur_hour = now.strftime("%Y-%m-%d %H")
    if meta.get("last_heartbeat") != cur_hour and now.minute < 4:
        set_meta("last_heartbeat", cur_hour)
        log.info(f"[HEARTBEAT] Open: {len(open_positions_snapshot())} | Equity: {get_equity(ex)}")


# ==========================================
# الحلقة الرئيسية
# ==========================================
def trading_loop():
    log.info(f"[INIT] Starting {VERSION} (Binance USDT-M Futures) ...")
    if not USE_SANDBOX and not ALLOW_LIVE_TRADING:
        log.error("[INIT] Live trading requires ALLOW_LIVE_TRADING=true")
        return
    if not BINANCE_API_KEY or not BINANCE_SECRET:
        log.error("[INIT] Missing BINANCE_API_KEY / BINANCE_SECRET")
        return
    ai_state.load_local_ai()
    start_sender()

    try:
        ex = init_exchange()
        load_state()
        migrate_journal()
        balance = get_equity(ex, force=True)
        mode_str = "تجريبي (Sandbox)" if USE_SANDBOX else "حقيقي"
        log.info(f"[BINANCE] Connected ({mode_str}). Equity: {balance}")
    except Exception as e:
        send(f"❌ فشل الاتصال بباينانس: {e}")
        return

    active_symbols = discover_universe(ex)
    active_symbols = list(dict.fromkeys(active_symbols + list(open_positions_snapshot())))
    universe_refresh_ts = time.time()
    sync_positions(ex, active_symbols)
    threading.Thread(target=telegram_listener, args=(active_symbols,), daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    send(f"🚀 <b>تم تشغيل البوت {VERSION} (Binance)</b>\n━━━━━━━━━━━━━━━\n"
         f"⚙️ الوضع: {mode_str}\n🪙 العملات: {len(active_symbols)}\n"
         f"📊 أقصى صفقات: {EFFECTIVE_MAX_OPEN} (نفس الاتجاه: {EFFECTIVE_MAX_SAME_DIR})\n"
         f"💰 المخاطرة: {RISK_PCT_PER_TRADE}% | رافعة: {LEVERAGE}x\n"
         f"🎚️ البروفايل: {PROFILE_NAME} | القناص: {'مفعّل' if ENABLE_SNIPER else 'معطّل'}\n"
         f"🧠 AI المحلي: {'نشط ✅' if ai_state.AI_IS_ACTIVE else 'معطّل'} | حد الدرجة: {MIN_SIGNAL_SCORE}/100\n"
         f"🔎 /why أسباب | ⚙️ /config إعدادات | 📱 /help\n━━━━━━━━━━━━━━━")
    if PROFILE_WARNING:
        send(f"⚠️ {PROFILE_WARNING}")

    loop_info["last_cycle_ts"] = time.time()
    last_orphan_check = time.time()
    last_backup_check = time.time()
    fail_streak = 0
    while True:
        start = time.time()
        cycle_errors = 0
        try:
            if time.time() - universe_refresh_ts >= UNIVERSE_REFRESH_SEC:
                refreshed = discover_universe(ex)
                if refreshed:
                    held = list(open_positions_snapshot())
                    active_symbols[:] = list(dict.fromkeys(refreshed + held))
                    universe_refresh_ts = time.time()
            refresh_prices(ex, active_symbols)
            if USE_BTC_FILTER:
                btc_bias(ex)
            held_symbols = open_positions_snapshot()
            collected_candidates = []
            for sym in sorted(active_symbols, key=lambda s: s not in held_symbols):
                try:
                    process_symbol(ex, sym, collected_candidates=collected_candidates)
                except Exception as e:
                    cycle_errors += 1
                    loop_info["last_error"] = f"{sym}: {str(e)[:150]}"
                    log.error(f"[LOOP] {sym}: {e}")

            if collected_candidates:
                ranked_candidates = sorted(
                    collected_candidates,
                    key=lambda item: (item.get("score", 0), item.get("ai_prob") or 0.0),
                    reverse=True,
                )
                for cand in ranked_candidates:
                    try:
                        execute_ranked_entry(ex, cand)
                    except Exception as e:
                        cycle_errors += 1
                        loop_info["last_error"] = f"{cand.get('sym')}: {str(e)[:150]}"
                        log.error(f"[RANKED ENTRY] {cand.get('sym')}: {e}")

            if time.time() - last_orphan_check >= ORPHAN_CHECK_SEC:
                adopt_orphans(ex, active_symbols)
                last_orphan_check = time.time()
            if time.time() - last_backup_check >= BACKUP_INTERVAL_SEC:
                backup_to_telegram()
                last_backup_check = time.time()
            maybe_send_daily_report(ex)
            maybe_send_heartbeat(ex)
        except Exception as e:
            cycle_errors += 1
            loop_info["last_error"] = str(e)[:150]
            log.error(f"[LOOP FATAL] {e}")
        loop_info["cycles"] += 1
        loop_info["last_cycle_ts"] = time.time()
        loop_info["last_duration"] = time.time() - start
        fail_streak = fail_streak + 1 if cycle_errors >= 5 else 0
        if fail_streak >= 3:
            try:
                ex = init_exchange()
                fail_streak = 0
            except Exception as e:
                log.error(f"[RECONNECT FAIL] {e}")
        time.sleep(max(8, SCAN_INTERVAL - (time.time() - start)))


def ensure_background_started():
    global _bg_started
    with _bg_lock:
        if _bg_started:
            return
        os.makedirs(DATA_DIR, exist_ok=True)
        if not acquire_single_instance():
            log.warning("[SINGLETON] Another bot instance holds lock; skipping background loop.")
            return
        _bg_started = True
        threading.Thread(target=trading_loop, daemon=True).start()


if __name__ != "__main__":
    ensure_background_started()


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    if not acquire_single_instance():
        log.error("نسخة أخرى من البوت تعمل بالفعل — إيقاف.")
        sys.exit(1)
    _bg_started = True
    threading.Thread(target=run_flask, daemon=True).start()
    trading_loop()
```[cite: 4, 5, 6]

تم التحقق من سلامة الصياغة البرمجية (`syntax`) للكود كاملاً، وهو جاهز للرفع إلى المستودع مع بقاء `ENABLE_SMC_FILTER=false` حتى اكتمال العينة الحالية.
