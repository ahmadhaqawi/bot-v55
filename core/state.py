# -*- coding: utf-8 -*-
"""State Management — single source of truth for all bot state."""
import os
import json
import logging
import threading
import tempfile
from datetime import datetime

from config import STATE_FILE, KSA_TZ

log = logging.getLogger("state")

state_lock = threading.RLock()

state = {}
meta = {"paused": False, "last_report": "", "loss_alert": "",
        "profit_alert": "", "last_heartbeat": ""}
daily_pnl = {"value": 0.0, "trades": 0, "wins": 0,
             "date": str(datetime.now(KSA_TZ).date())}


# ==========================================
# Schema
# ==========================================
def new_sym_state():
    return {
        "position": None, "entry": None, "sl": None, "tp": None,
        "initial_sl": None, "initial_risk": 0.0,
        "contracts": 0.0, "initial_contracts": 0.0, "margin": 0.0,
        "opened_ts": 0, "leg_ts": 0, "last_exit_ts": 0, "last_exit_mode": "swing",
        "realized_pnl": 0.0, "realized_fees_est": 0.0,
        "breakeven_hit": False,
        "partial_1_taken": False, "partial_2_taken": False,
        "last_notified_sl": None,
        "regime": "unknown", "strategy": "", "mode": "swing",
        "planned_entry": None, "missing_checks": 0,
        "signal_score": 0, "score_factors": {}, "volume_ratio": 0, "ker": None,
        "pending_protection_sl": None, "partial_order_pending": None,
    }


# ==========================================
# Load / Save
# ==========================================
def _atomic_write(path, payload):
    d = os.path.dirname(os.path.abspath(path)) or "."
    with tempfile.NamedTemporaryFile(dir=d, delete=False) as f:
        tmp = f.name
    try:
        with open(tmp, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_state():
    global state, daily_pnl
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        loaded = {}
        for k, v in data.get("state", {}).items():
            base = new_sym_state()
            base.update(v)
            loaded[k.split("|")[0]] = base
        with state_lock:
            state.clear()
            state.update(loaded)
            daily_pnl.clear()
            daily_pnl.update(data.get("daily_pnl", daily_pnl))
            meta.update(data.get("meta", {}))
    except Exception as e:
        log.error(f"[STATE LOAD ERROR] {e}")


def save_state():
    try:
        with state_lock:
            compact_state = {
                k: v for k, v in state.items()
                if v.get("position") is not None or (v.get("last_exit_ts") or 0) > 0
            }
            txt = json.dumps(
                {"state": compact_state, "daily_pnl": daily_pnl, "meta": meta},
                ensure_ascii=False, indent=2,
            )
            _atomic_write(STATE_FILE, txt.encode("utf-8"))
    except Exception as e:
        log.error(f"[STATE SAVE ERROR] {e}")


# ==========================================
# Per-Symbol Access
# ==========================================
def get_sym_state(sym):
    """يُرجع نسخة (dict) — لا يعرّض الكائن الأصلي للخطر."""
    with state_lock:
        if sym not in state:
            state[sym] = new_sym_state()
        return dict(state[sym])


def update_sym_state(sym, updates):
    """يدمج التحديثات في الحالة ويحفظ."""
    with state_lock:
        if sym not in state:
            state[sym] = new_sym_state()
        state[sym].update(updates)
    save_state()


def reset_sym_state(sym, cooldown=True, mode=None):
    with state_lock:
        s = new_sym_state()
        s["last_exit_ts"] = datetime.now(KSA_TZ).timestamp() if cooldown else 0
        s["last_exit_mode"] = mode
        state[sym] = s
    save_state()


def get_state_snapshot():
    """قائمة كل الرموز التي لها حالة."""
    with state_lock:
        return {k: dict(v) for k, v in state.items()}


def open_positions_snapshot():
    with state_lock:
        return {k: dict(v) for k, v in state.items() if v.get("position")}


# ==========================================
# Meta
# ==========================================
def set_meta(key, value):
    with state_lock:
        meta[key] = value
    save_state()


def get_meta(key, default=None):
    with state_lock:
        return meta.get(key, default)


def is_paused():
    with state_lock:
        return bool(meta.get("paused"))


# ==========================================
# Daily PnL
# ==========================================
def _roll_daily_locked():
    """يجب أن يُستدعى تحت state_lock."""
    today = str(datetime.now(KSA_TZ).date())
    if daily_pnl.get("date") != today:
        daily_pnl.clear()
        daily_pnl.update({"value": 0.0, "trades": 0, "wins": 0, "date": today})


def add_daily_pnl(pnl_usdt):
    with state_lock:
        _roll_daily_locked()
        daily_pnl["value"] += pnl_usdt
    save_state()


def register_trade_result(total_pnl):
    with state_lock:
        _roll_daily_locked()
        daily_pnl["trades"] += 1
        if total_pnl > 0:
            daily_pnl["wins"] += 1
    save_state()


def daily_snapshot():
    with state_lock:
        _roll_daily_locked()
        return dict(daily_pnl)
