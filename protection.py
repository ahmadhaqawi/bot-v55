# protection.py — إدارة أوامر الحماية الشرطية (SL/TP) وفتح وإغلاق المراكز (Binance Algo Service)
import math
import time
import logging

from config import (
    LEVERAGE,
    MARGIN_MODE,
)
from exchange import (
    calculate_contracts,
    get_open_position,
)
from telegram.notifier import (
    send,
    short,
)

log = logging.getLogger("bot")


def _valid_trigger(value):
    try:
        v = float(value or 0)
        return math.isfinite(v) and v > 0
    except (TypeError, ValueError):
        return False


def get_algos(ex, sym):
    """الأوامر الشرطية المعلّقة (SL/TP) من Binance Algo Service."""
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
        out.append({
            "id": o.get('algoId'),
            "side": str(o.get("side") or "").lower(),
            "size": o.get("quantity"),
            "reduce_only": str(o.get("reduceOnly")).lower() == "true",
            "is_sl": otype in ("STOP_MARKET", "STOP"),
            "is_tp": otype in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT"),
        })
    return out


def has_protection(ex, sym, close_side, contracts):
    """(has_sl, has_tp) أو (None, None) إذا تعذر الاستعلام."""
    try:
        algos = [
            a for a in get_algos(ex, sym)
            if a["id"] and a["side"] == close_side
            and a["reduce_only"] and _valid_trigger(a["size"])
            and math.isclose(float(a["size"]), contracts, rel_tol=1e-9, abs_tol=1e-12)
        ]
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
    req = {
        'algoType': 'CONDITIONAL',
        'symbol': market['id'],
        'side': side.upper(),
        'type': 'STOP_MARKET' if is_sl else 'TAKE_PROFIT_MARKET',
        'quantity': amt_str,
        'triggerPrice': trig_str,
        'reduceOnly': 'true',
        'workingType': 'CONTRACT_PRICE',
    }
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
    ids = [
        a["id"] for a in old
        if a["id"] and a["reduce_only"] and a["side"] == close_side
        and (a["is_sl"] or a["is_tp"])
        and (not a["is_tp"] or (tp is not None and tp_ok))
    ]
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
