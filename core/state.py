# State Management

def new_sym_state():
    return {
        "position": None, "entry": None, "sl": None, "tp": None,
        "initial_sl": None, "initial_risk": 0.0,
        "contracts": 0.0, "initial_contracts": 0.0, "margin": 0.0,
        "opened_ts": 0, "leg_ts": 0, "last_exit_ts": 0, "last_exit_mode": "swing",
        "realized_pnl": 0.0, "breakeven_hit": False,
        "partial_1_taken": False, "partial_2_taken": False,
        "last_notified_sl": None, "regime": "unknown", "strategy": "",
        "planned_entry": None, "realized_fees_est": 0.0, "missing_checks": 0,
        "signal_score": 0, "score_factors": {}, "volume_ratio": 0, "ker": None, "mode": "swing",
        "pending_protection_sl": None, "partial_order_pending": None,
    }


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
            state = loaded
            daily_pnl = data.get("daily_pnl", daily_pnl)
            meta.update(data.get("meta", {}))
    except Exception as e:
        log.error(f"[STATE LOAD ERROR] {e}")


def _atomic_write(path, payload):
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(os.path.abspath(path)), delete=False) as f:
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

def save_state():
    try:
        with state_lock:
            compact_state = {
                k: v for k, v in state.items()
                if v.get("position") is not None or (v.get("last_exit_ts") or 0) > 0
            }
            txt = json.dumps({"state": compact_state, "daily_pnl": daily_pnl, "meta": meta},
                             ensure_ascii=False, indent=2)
            _atomic_write(STATE_FILE, txt.encode("utf-8"))
    except Exception as e:
        log.error(f"[STATE SAVE ERROR] {e}")
