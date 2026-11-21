# exchange.py — طبقة المنصة (Binance USDT-M Futures): اتصال، أسعار، رصيد، مراكز، وسجل التنفيذات
import math
import time
import logging
import threading

import ccxt

from config import (
    BINANCE_API_KEY,
    BINANCE_SECRET,
    USE_SANDBOX,
    ALLOW_LIVE_TRADING,
)

log = logging.getLogger("bot")

# الكاشات المشتركة
_price_cache = {}
_balance_cache = {}
_balance_lock = threading.Lock()

# أسماء دوال ccxt الضمنية لـ Binance Algo Service (SL/TP)
_ALGO_METHODS = (
    "fapiPrivatePostAlgoOrder",
    "fapiPrivateDeleteAlgoOrder",
    "fapiPrivateGetOpenAlgoOrders",
)


def _valid_trigger(value):
    try:
        v = float(value or 0)
        return math.isfinite(v) and v > 0
    except (TypeError, ValueError):
        return False


def create_exchange():
    if not USE_SANDBOX and not ALLOW_LIVE_TRADING:
        raise RuntimeError("Live trading requires ALLOW_LIVE_TRADING=true")
    ex = ccxt.binanceusdm({
        "apiKey": BINANCE_API_KEY,
        "secret": BINANCE_SECRET,
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
        "timeout": 20000,
    })
    if USE_SANDBOX:
        ex.set_sandbox_mode(True)
    return ex


def verify_algo_support(ex):
    """التحقق من دعم ccxt لـ Binance Algo Service (الأوامر الشرطية SL/TP)."""
    missing = [m for m in _ALGO_METHODS if not hasattr(ex, m)]
    if missing:
        raise RuntimeError(
            f"ccxt الحالية لا تدعم Binance Algo endpoints: {missing} — "
            "حدّثها: pip install -U ccxt"
        )


def init_exchange():
    ex = create_exchange()
    ex.load_markets()
    verify_algo_support(ex)
    return ex


def refresh_prices(ex, symbols):
    try:
        tickers = ex.fetch_tickers(symbols)
        now = time.time()
        for s, t in tickers.items():
            last = t.get("last")
            if last:
                bid = float(t["bid"]) if _valid_trigger(t.get("bid")) else None
                ask = float(t["ask"]) if _valid_trigger(t.get("ask")) else None
                _price_cache[s] = (now, float(last), bid, ask)
    except Exception as e:
        log.warning(f"[PRICES] {e}")


def get_current_price(ex, sym, max_age=20):
    c = _price_cache.get(sym)
    if c and max_age > 0 and time.time() - c[0] <= max_age:
        return c[1]
    try:
        t = ex.fetch_ticker(sym)
        p = float(t["last"])
        if not _valid_trigger(p):
            return None
        bid = float(t["bid"]) if _valid_trigger(t.get("bid")) else None
        ask = float(t["ask"]) if _valid_trigger(t.get("ask")) else None
        _price_cache[sym] = (time.time(), p, bid, ask)
        return p
    except Exception as e:
        log.warning(f"[PRICE] {sym}: {e}")
        return c[1] if c and max_age > 0 and time.time() - c[0] <= max_age else None


def get_balances(ex, force=False):
    """رصيد الحساب الإجمالي للمخاطرة، والمتاح فعلياً كسقف للتنفيذ."""
    with _balance_lock:
        cached = _balance_cache.get(ex)
    if not force and cached and time.monotonic() - cached[0] < 60:
        return cached[1], cached[2]
    try:
        bal = ex.fetch_balance().get("USDT") or {}
        free = float(bal.get("free") or 0)
        equity = float(bal["total"] if bal.get("total") is not None else free)
        if not all(math.isfinite(v) for v in (free, equity)):
            raise ValueError("non-finite USDT balance")
        values = max(0.0, equity), max(0.0, free)
        with _balance_lock:
            if len(_balance_cache) >= 8:
                _balance_cache.clear()
            _balance_cache[ex] = (time.monotonic(), *values)
        return values
    except Exception as e:
        with _balance_lock:
            _balance_cache.pop(ex, None)
        log.warning("[EQUITY] %s", type(e).__name__)
        return 0.0, 0.0


def get_equity(ex, force=False):
    return get_balances(ex, force)[0]


def contract_size(ex, sym):
    try:
        return float(ex.market(sym).get("contractSize") or 1)
    except Exception:
        return 1.0


def min_amount(ex, sym):
    try:
        return float(ex.market(sym).get("limits", {}).get("amount", {}).get("min") or 1.0)
    except Exception:
        return 1.0


def calculate_contracts(ex, sym, margin, price, lev):
    try:
        market = ex.market(sym)
        raw = (margin * lev) / (price * float(market.get("contractSize") or 1))
        amt = float(ex.amount_to_precision(sym, raw))
        return amt if amt >= min_amount(ex, sym) else 0.0
    except Exception as e:
        log.warning(f"[AMOUNT] {sym}: {e}")
        return 0.0


def get_open_position(ex, sym):
    """None فقط إذا لا يوجد مركز. يتطلب وضع One-way في Binance (positionSide = BOTH)."""
    matches = []
    for p in ex.fetch_positions([sym]):
        if p.get("symbol") not in (None, sym):
            continue
        contracts = abs(float(p.get("contracts") or 0))
        if contracts > 0:
            if p.get("side") not in ("long", "short") or (p.get("info") or {}).get("positionSide") in ("LONG", "SHORT"):
                raise ValueError("the bot requires Binance one-way position mode")
            matches.append({
                "side": p["side"].upper(),
                "contracts": contracts,
                "entry": float(p.get("entryPrice") or 0),
                "margin": float(p.get("initialMargin") or 0),
            })
    if len(matches) > 1:
        raise ValueError("multiple positions for one instrument")
    return matches[0] if matches else None


def get_actual_fees(ex, sym, pos_data):
    """مجموع الرسوم الفعلية (USDT). None إذا لا تتوفر أو للمركز المتبنّى."""
    if (pos_data.get("strategy") or "") == "adopted" or not pos_data.get("opened_ts"):
        return None
    try:
        since = int(pos_data["opened_ts"] * 1000) - 30000
        trades = ex.fetch_my_trades(sym, since=since, limit=100)
        total, n = 0.0, 0
        for t in trades:
            fee = t.get("fee") or {}
            cost = fee.get("cost")
            if cost is None:
                continue
            cur = (fee.get("currency") or "").upper()
            if cur and cur not in ("USDT", "USDC"):
                continue
            total += abs(float(cost))
            n += 1
        return total if n > 0 else None
    except Exception as e:
        log.warning(f"[FEES] {sym}: {e}")
        return None


def get_exit_price(ex, sym, pos_data):
    try:
        close_side = "sell" if pos_data["position"] == "LONG" else "buy"
        if pos_data.get("partial_1_taken") or pos_data.get("partial_2_taken"):
            since = int((pos_data.get("leg_ts") or 0) * 1000) + 1500
        else:
            since = int((pos_data.get("opened_ts") or 0) * 1000) - 8000
        trades = ex.fetch_my_trades(sym, since=since, limit=80)
        trades = [t for t in trades if not t.get("timestamp") or t["timestamp"] >= since]
        fills = [t for t in trades if t.get("side") == close_side and t.get("price") and t.get("amount")]
        total = sum(float(t["amount"]) for t in fills)
        if total > 0:
            return sum(float(t["price"]) * float(t["amount"]) for t in fills) / total
    except Exception as e:
        log.warning(f"[EXIT PRICE] {sym}: {e}")
    return None
