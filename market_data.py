import time
import logging
import pandas as pd

from config import (
    KER_PERIOD,
    TF_MS
)

from indicators import (
    add_indicators,
    calculate_range_filter,
    efficiency_ratio
)

log = logging.getLogger("bot")

_data_cache = {}

_btc_bias_cache = {
    "ts": 0,
    "value": "neutral"
}


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
