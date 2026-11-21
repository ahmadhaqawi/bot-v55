# exchange.py — طبقة المنصة (Binance USDT-M Futures): اتصال، أسعار، رصيد، أوامر، حماية، بيانات
# لا تحتوي أي منطق استراتيجية. تعتمد فقط على notifier و indicators و config.
import math
import time
import logging
import threading

import ccxt
import pandas as pd

from config import (
    BINANCE_API_KEY, BINANCE_SECRET, USE_SANDBOX, ALLOW_LIVE_TRADING,
    LEVERAGE, MARGIN_MODE, KER_PERIOD, TF_MS,
)
from telegram.notifier import send, short
from indicators import add_indicators, calculate_range_filter, efficiency_ratio

log = logging.getLogger("bot")

# الكاشات تعيش هنا، والملفات الأخرى تستوردها بالاسم (نفس الكائن، لا نسخة)
_data_cache = {}
_price_cache = {}
_balance_cache = {}
_balance_lock = threading.Lock()
_btc_bias_cache = {"ts": 0, "value": "neutral"}

# أسماء دوال ccxt الضمنية لـ Binance Algo Service (POST/DELETE /fapi/v1/algoOrder, GET open algo orders)
_ALGO_METHODS = ("fapiPrivatePostAlgoOrder", "fapiPrivateDeleteAlgoOrder", "fapiPrivateGetOpenAlgoOrders")


def create_exchange():
    if not USE_SANDBOX and not ALLOW_LIVE_TRADING:
        raise RuntimeError("Live trading requires ALLOW_LIVE_TRADING=true")
    ex = ccxt.binanceusdm({
        'apiKey': BINANCE_API_KEY, 'secret': BINANCE_SECRET,
        'enableRateLimit': True, 'options': {'defaultType': 'future'}, 'timeout': 20000,
    })
    if USE_SANDBOX:
        ex.set_sandbox_mode(True)
    return ex


def init_exchange():
    ex = create_exchange()
    ex.load_markets()
    verify_algo_support(ex)
    return ex


def verify_algo_support(ex):
    """Binance نقل الأوامر الشرطية (SL/TP) إلى Algo Service منذ ديسمبر 2025.
    نتأكد عند البدء أن ccxt المثبتة تعرف هذه الـ endpoints، وإلا نفشل فوراً
    بدل اكتشاف ذلك عند أول صفقة حقيقية."""
    missing = [m for m in _ALGO_METHODS if not hasattr(ex, m)]
    if missing:
        raise RuntimeError(
            f"ccxt الحالية لا تدعم Binance Algo endpoints: {missing} — "
            "حدّثها: pip install -U ccxt")


def refresh_prices(ex, symbols):
    try:
        tickers = ex.fetch_tickers(symbols)
        now = time.time()
        for s, t in tickers.items():
            last = t.get('last')
            if last:
                bid = float(t['bid']) if _valid_trigger(t.get('bid')) else None
                ask = float(t['ask']) if _valid_trigger(t.get('ask')) else None
                _price_cache[s] = (now, float(last), bid, ask)
    except Exception as e:
        log.warning(f"[PRICES] {e}")


def get_current_price(ex, sym, max_age=20):
    c = _price_cache.get(sym)
    if c and max_age > 0 and time.time() - c[0] <= max_age:
        return c[1]
    try:
        t = ex.fetch_ticker(sym)
        p = float(t['last'])
        if not _valid_trigger(p):
            return None
        bid = float(t['bid']) if _valid_trigger(t.get('bid')) else None
        ask = float(t['ask']) if _valid_trigger(t.get('ask')) else None
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
        return float(ex.market(sym).get('contractSize') or 1)
    except Exception:
        return 1.0


def min_amount(ex, sym):
    try:
        return float(ex.market(sym).get('limits', {}).get('amount', {}).get('min') or 1.0)
    except Exception:
        return 1.0


def calculate_contracts(ex, sym, margin, price, lev):
    try:
        market = ex.market(sym)
        raw = (margin * lev) / (price * float(market.get('contractSize') or 1))
        amt = float(ex.amount_to_precision(sym, raw))
        return amt if amt >= min_amount(ex, sym) else 0.0
    except Exception as e:
        log.warning(f"[AMOUNT] {sym}: {e}")
        return 0.0


def get_open_position(ex, sym):
    """None فقط إذا لا يوجد مركز. أي خطأ API يرفع Exception.
    يتطلب وضع One-way في Binance (positionSide = BOTH)."""
    matches = []
    for p in ex.fetch_positions([sym]):
        if p.get("symbol") not in (None, sym):
            continue
        contracts = abs(float(p.get('contracts') or 0))
        if contracts > 0:
            if p.get("side") not in ("long", "short") or (p.get("info") or {}).get("positionSide") in ("LONG", "SHORT"):
                raise ValueError("the bot requires Binance one-way position mode")
            matches.append({"side": p["side"].upper(), "contracts": contracts,
                            "entry": float(p.get('entryPrice') or 0),
                            "margin": float(p.get('initialMargin') or 0)})
    if len(matches) > 1:
        raise ValueError("multiple positions for one instrument")
    return matches[0] if matches else None


def _valid_trigger(value):
    try:
        v = float(value or 0)
        return math.isfinite(v) and v > 0
    except (TypeError, ValueError):
        return False


def get_algos(ex, sym):
    """الأوامر الشرطية المعلّقة (SL/TP) من Binance Algo Service.
    نُعيد نفس الشكل الذي تتوقعه بقية الدوال (side بحروف صغيرة)."""
    market = ex.market(sym)
    res = ex.fapiPrivateGetOpenAlgoOrders({'symbol': market['id']})
    if isinstance(res, dict):
        res = res.get('orders', res.get('data'))
    if not isinstance(res, list):
        raise ValueError("invalid Binance open algo orders response")
    out = []
    for o in res:
        if str(o.get('algoType') or 'CONDITIONAL').upper() != 'CONDITIONAL':
            continue
        otype = str(o.get('orderType') or o.get('type') or '').upper()
        out.append({"id": o.get('algoId'),
                    "side": str(o.get("side") or "").lower(),
                    "size": o.get("quantity"),
                    "reduce_only": str(o.get("reduceOnly")).lower() == "true",
                    "is_sl": otype in ("STOP_MARKET", "STOP"),
                    "is_tp": otype in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT")})
    return out


def has_protection(ex, sym, close_side, contracts):
    """(has_sl, has_tp) أو (None, None) إذا تعذر الاستعلام."""
    try:
        algos = [a for a in get_algos(ex, sym) if a["id"] and a["side"] == close_side
                 and a["reduce_only"] and _valid_trigger(a["size"])
                 and math.isclose(float(a["size"]), contracts, rel_tol=1e-9, abs_tol=1e-12)]
        return any(a["is_sl"] for a in algos), any(a["is_tp"] for a in algos)
    except Exception as e:
        log.warning(f"[PROTECTION UNKNOWN] {sym}: {e}")
        return None, None


def cancel_algo_ids(ex, sym, ids):
    for aid in ids:
        try:
            ex.fapiPrivateDeleteAlgoOrder({'algoId': aid})
        except Exception as e:
            log.warning(f"[CANCEL ALGO] {sym} {aid}: {e}")


def cancel_pending(ex, sym):
    try:
        cancel_algo_ids(ex, sym, [a["id"] for a in get_algos(ex, sym)])
        for o in ex.fetch_open_orders(sym):
            try:
                ex.cancel_order(o['id'], sym)
            except Exception:
                pass
    except Exception as e:
        log.warning(f"[CANCEL PENDING] {sym}: {e}")


def place_sl_tp(ex, sym, side, contracts, trigger, is_sl):
    trig_str = str(ex.price_to_precision(sym, trigger))
    amt_str = str(ex.amount_to_precision(sym, contracts))
    market = ex.market(sym)
    req = {'algoType': 'CONDITIONAL', 'symbol': market['id'], 'side': side.upper(),
           'type': 'STOP_MARKET' if is_sl else 'TAKE_PROFIT_MARKET',
           'quantity': amt_str, 'triggerPrice': trig_str,
           'reduceOnly': 'true', 'workingType': 'CONTRACT_PRICE'}
    res = ex.fapiPrivatePostAlgoOrder(req)
    if not isinstance(res, dict) or not res.get("algoId"):
        raise Exception(f"algo rejected: {res}")
    return res


def protect_position(ex, sym, close_side, contracts, sl, tp, retries=3):
    """وقف الخسارة: أي رفض = فشل (لا نعتبر الصفقة محمية)."""
    sl_ok = tp_ok = False
    if sl is not None:
        for i in range(retries):
            try:
                place_sl_tp(ex, sym, close_side, contracts, sl, True)
                sl_ok = True
                break
            except Exception as e:
                log.error(f"[SL ERR] {sym} try {i + 1}: {e}")
                time.sleep(0.8)
    if tp is not None and contracts > 0:
        for i in range(retries):
            try:
                place_sl_tp(ex, sym, close_side, contracts, tp, False)
                tp_ok = True
                break
            except Exception as e:
                if "-2021" in str(e) or "immediately trigger" in str(e).lower():
                    log.warning(f"[TP SKIP] {sym}: target already passed")
                    break
                log.error(f"[TP ERR] {sym} try {i + 1}: {e}")
                time.sleep(0.8)
    return sl_ok, tp_ok


def replace_protection(ex, sym, close_side, contracts, sl, tp=None):
    """نضع الجديد أولاً ثم نلغي القديم. فشل الوقف الجديد = تبقى القديمة."""
    try:
        old = get_algos(ex, sym)
    except Exception as e:
        log.warning(f"[REPLACE] list failed {sym}: {e}")
        return False
    sl_ok, tp_ok = protect_position(ex, sym, close_side, contracts, sl, tp)
    if not sl_ok:
        return False
    ids = [a["id"] for a in old if a["id"] and a["reduce_only"] and a["side"] == close_side
           and (a["is_sl"] or a["is_tp"]) and (not a["is_tp"] or (tp is not None and tp_ok))]
    cancel_algo_ids(ex, sym, ids)
    return True


def close_full(ex, sym):
    """إغلاق كامل مع تأكيد متكرر."""
    try:
        pos = get_open_position(ex, sym)
        if pos is None:
            cancel_pending(ex, sym)
            return True
        close_side = 'sell' if pos["side"] == "LONG" else 'buy'
        ex.create_order(sym, 'market', close_side, pos["contracts"], None, {'reduceOnly': True})
        for _ in range(6):
            time.sleep(0.8)
            if get_open_position(ex, sym) is None:
                cancel_pending(ex, sym)
                return True
        log.error(f"[CLOSE FULL] {sym}: المركز ما زال ظاهراً")
        return False
    except Exception as e:
        log.error(f"[CLOSE FULL ERR] {sym}: {e}")
        return False


def set_leverage_checked(ex, sym, leverage=None):
    """Binance: نضبط نوع الهامش (isolated) ثم الرافعة. خطأ -4046 يعني أنه مضبوط مسبقاً."""
    lev = leverage if leverage is not None else LEVERAGE
    last_err = None
    for _ in range(2):
        try:
            try:
                ex.set_margin_mode(MARGIN_MODE.lower(), sym)
            except Exception as e:
                if "-4046" not in str(e) and "No need to change margin type" not in str(e):
                    raise
            ex.set_leverage(lev, sym)
            return True, ""
        except Exception as e:
            last_err = e
            time.sleep(0.6)
    return False, str(last_err)


def open_position(ex, sym, side, price, sl, tp, margin, leverage=None):
    try:
        lev = leverage if leverage is not None else LEVERAGE
        amount = calculate_contracts(ex, sym, margin, price, lev)
        if amount <= 0:
            return False, "كمية غير صالحة", price, 0.0
        cancel_pending(ex, sym)
        ok, err = set_leverage_checked(ex, sym, lev)
        if not ok:
            log.error(f"[LEVERAGE] {sym}: {err}")
            return False, f"تعذر ضبط الرافعة: {err[:120]}", price, 0.0
        order_side = 'buy' if side == "LONG" else 'sell'
        close_side = 'sell' if side == "LONG" else 'buy'
        order = ex.create_order(sym, 'market', order_side, amount, None)
        pos = None
        for _ in range(7):
            time.sleep(0.7)
            try:
                pos = get_open_position(ex, sym)
            except Exception as e:
                log.warning(f"[POS FETCH] {sym}: {e}")
                pos = None
            if pos:
                break
        if not pos:
            log.error(f"[OPEN UNCONFIRMED] {sym}: order={order.get('id')}")
            return False, "لم يتأكد تنفيذ الأمر (لا مركز) — لم تُوضع حماية", price, 0.0
        entry = pos["entry"] or price
        contracts = pos["contracts"]
        sl_ok, tp_ok = protect_position(ex, sym, close_side, contracts, sl, tp)
        if not sl_ok:
            send(f"🚨 فشل وضع SL لـ <code>{short(sym)}</code> — جاري الإغلاق.")
            if not close_full(ex, sym):
                send(f"🆘 فشل إغلاق <code>{short(sym)}</code>! تدخل يدوي فوراً.")
            return False, "فشل وضع SL", entry, 0.0
        if not tp_ok:
            send(f"⚠️ تم SL لكن فشل TP لـ <code>{short(sym)}</code>")
        return True, order.get('id'), entry, contracts
    except Exception as e:
        log.error(f"[OPEN ERR] {sym}: {e}")
        return False, str(e)[:150], price, 0.0


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
        close_side = 'sell' if pos_data["position"] == "LONG" else 'buy'
        if pos_data.get("partial_1_taken") or pos_data.get("partial_2_taken"):
            since = int((pos_data.get("leg_ts") or 0) * 1000) + 1500
        else:
            since = int((pos_data.get("opened_ts") or 0) * 1000) - 8000
        trades = ex.fetch_my_trades(sym, since=since, limit=80)
        trades = [t for t in trades if not t.get("timestamp") or t["timestamp"] >= since]
        fills = [t for t in trades if t.get('side') == close_side and t.get('price') and t.get('amount')]
        total = sum(float(t['amount']) for t in fills)
        if total > 0:
            return sum(float(t['price']) * float(t['amount']) for t in fills) / total
    except Exception as e:
        log.warning(f"[EXIT PRICE] {sym}: {e}")
    return None


def fetch_data(ex, sym, tf, limit=130):
    """يعيد مؤشرات محسوبة مرة لكل شمعة مغلقة، مع حذف FIFO عند امتلاء الكاش."""
    key = (sym, tf, limit)
    tf_ms = TF_MS[tf]
    cached = _data_cache.get(key)
    current = int(time.time() * 1000 // tf_ms) * tf_ms
    if cached and cached[0] == current:
        return cached[1]
    try:
        data = ex.fetch_ohlcv(sym, tf, limit=limit)
        current = int(time.time() * 1000 // tf_ms) * tf_ms
        if len(data) < 2 or data[-1][0] != current or data[-2][0] != current - tf_ms:
            return pd.DataFrame()
        df = to_df(data)
        if tf in ("5m", "15m") and len(df) >= 70:
            df.attrs["range_filter"] = calculate_range_filter(df)
            df["ker_indicator"] = efficiency_ratio(df["close"], KER_PERIOD)
        if len(_data_cache) >= 1024 and key not in _data_cache:
            for _ in range(150):
                _data_cache.pop(next(iter(_data_cache)), None)
        _data_cache[key] = (current, df)
        return df
    except Exception as e:
        log.warning(f"[OHLCV] {sym} {tf}: {e}")
        return pd.DataFrame()


def to_df(raw):
    return add_indicators(pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"]))


def btc_bias(ex):
    if time.time() - _btc_bias_cache["ts"] < 120:
        return _btc_bias_cache["value"]
    val = "neutral"
    try:
        df = fetch_data(ex, "BTC/USDT:USDT", "1h")
        if len(df) >= 60:
            r = df.iloc[-2]
            if r["close"] > r["ema50"] and r["ema21"] > r["ema50"] and r["ema9"] > r["ema21"]:
                val = "up"
            elif r["close"] < r["ema50"] and r["ema21"] < r["ema50"] and r["ema9"] < r["ema21"]:
                val = "down"
    except Exception as e:
        log.warning(f"[BTC BIAS] {e}")
    _btc_bias_cache.update({"ts": time.time(), "value": val})
    return val
