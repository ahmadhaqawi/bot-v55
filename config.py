# -*- coding: utf-8 -*-
"""config.py — كل الثوابت وإعدادات البيئة (V55، منصة Binance USDT-M Futures).
ثوابت فقط: لا حالة تشغيل ولا أقفال ولا كاشات (تلك في core/state.py و exchange.py)."""
import os
import math
from datetime import timezone, timedelta

VERSION = "V55"



KSA_TZ = timezone(timedelta(hours=3))

def env_float(name, default):
    try:
        value = float(os.getenv(name, default))
        return value if math.isfinite(value) else float(default)
    except ValueError:
        return float(default)

def env_flag(name, default):
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes")

# ==========================================
# الإعدادات
# ==========================================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET = os.getenv("BINANCE_SECRET", "")
USE_SANDBOX = os.getenv("USE_SANDBOX", "true").lower() in ("true", "1", "yes")
ALLOW_LIVE_TRADING = env_flag("ALLOW_LIVE_TRADING", False)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
AUTHORIZED_CHATS = {c.strip() for c in TELEGRAM_CHAT_ID.split(",") if c.strip()}

FUTURES_SYMBOLS = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT", "XRP/USDT:USDT",
    "DOGE/USDT:USDT", "ADA/USDT:USDT", "AVAX/USDT:USDT", "LINK/USDT:USDT", "DOT/USDT:USDT",
    "NEAR/USDT:USDT", "APT/USDT:USDT", "SUI/USDT:USDT", "ARB/USDT:USDT", "OP/USDT:USDT",
    "INJ/USDT:USDT", "ATOM/USDT:USDT", "LTC/USDT:USDT", "BCH/USDT:USDT", "FIL/USDT:USDT",
    "SEI/USDT:USDT", "TIA/USDT:USDT", "STX/USDT:USDT", "IMX/USDT:USDT", "RUNE/USDT:USDT",
    "FET/USDT:USDT", "WLD/USDT:USDT", "PEPE/USDT:USDT", "1000PEPE/USDT:USDT", "WIF/USDT:USDT", "ORDI/USDT:USDT",
]

SECTORS = {
    "major": {"BTC", "ETH", "BNB"},
    "l1": {"SOL", "AVAX", "NEAR", "APT", "SUI", "ADA", "DOT", "ATOM", "SEI", "TIA", "INJ"},
    "l2": {"ARB", "OP", "STX", "IMX"},
    "meme": {"DOGE", "PEPE", "1000PEPE", "WIF"},
    "ai": {"FET", "WLD"},
    "legacy": {"XRP", "LTC", "BCH", "FIL", "LINK", "RUNE", "ORDI"},
}
MAX_PER_SECTOR_SAME_DIR = 2

SCAN_INTERVAL = int(env_float("SCAN_INTERVAL", 15))
UNIVERSE_REFRESH_SEC = int(env_float("UNIVERSE_REFRESH_SEC", 900))
MAX_UNIVERSE_SYMBOLS = int(env_float("MAX_UNIVERSE_SYMBOLS", 60))

ENTRY_MODE = os.getenv("ENTRY_MODE", "balanced").strip().lower()
if ENTRY_MODE not in ("balanced", "active", "strict"):
    ENTRY_MODE = "balanced"

_entry_defaults = {
    "strict":   {"min_ker": 0.30, "min_adx": 23, "min_score": 78, "bo_vol": 1.8},
    "balanced": {"min_ker": 0.25, "min_adx": 20, "min_score": 68, "bo_vol": 1.5},
    "active":   {"min_ker": 0.18, "min_adx": 18, "min_score": 60, "bo_vol": 1.2},
}[ENTRY_MODE]

MIN_KER = env_float("MIN_KER", _entry_defaults["min_ker"])
MIN_ADX_OVERRIDE = env_float("MIN_ADX", _entry_defaults["min_adx"])
MIN_SIGNAL_SCORE = int(env_float("MIN_SIGNAL_SCORE", _entry_defaults["min_score"]))
KER_PERIOD = int(env_float("KER_PERIOD", 10))
INCLUDE_NEW_MARKETS_DAYS = int(env_float("INCLUDE_NEW_MARKETS_DAYS", 0))
UNIVERSE_MODE = os.getenv("UNIVERSE_MODE", "dynamic").strip().lower()
EXCLUDE_BASES = {x.strip().upper() for x in os.getenv(
    "EXCLUDE_BASES",
    "ASML,QQQ,SPY,ANTHROPIC,OPENAI,NVDA,TSLA,AAPL,MSFT,GOOGL,AMZN,META,COIN,MSTR,CRCL,HOOD"
).split(",") if x.strip()}

ORPHAN_CHECK_SEC = int(env_float("ORPHAN_CHECK_SEC", 60))
ADOPT_ORPHANS = env_flag("ADOPT_ORPHANS", True)
GHOST_MAX_AGE_SEC = 120
MISSING_CONFIRMATIONS = 2

LEVERAGE = int(env_float("LEVERAGE", 5))
MARGIN_MODE = "isolated"
MAX_OPEN_POSITIONS = int(env_float("MAX_OPEN_POSITIONS", 6))
REENTRY_COOLDOWN_SEC = int(env_float("REENTRY_COOLDOWN_SEC", 1500))

USE_RISK_SIZING = True
RISK_PCT_PER_TRADE = env_float("RISK_PCT_PER_TRADE", 0.65)
POSITION_SIZE_USDT = 50
MIN_MARGIN_USDT = 10
MAX_MARGIN_USDT = env_float("MAX_MARGIN_USDT", 130)
MAX_MARGIN_PCT_EQUITY = min(1.0, max(0.0, env_float("MAX_MARGIN_PCT_EQUITY", 0.30)))

ATR_SL_MULT = 1.55
RR_RATIO = 2.3
MIN_SL_PCT = 0.0065
MAX_SL_PCT = 0.032
TRAIL_ATR_MULT = 1.05
BE_TRIGGER_R = 0.9
FALLBACK_SL_PCT = 0.017

PARTIAL1_R, PARTIAL1_PCT = 1.3, 0.40
PARTIAL2_R, PARTIAL2_PCT = 2.1, 0.50

FEE_RATE = 0.0005
BE_FEE_BUFFER = min(0.05, max(2 * FEE_RATE, env_float("BE_FEE_BUFFER", 0.0012)))
FLOATING_FUNDING_BUFFER_PCT = max(0.0, env_float("FLOATING_FUNDING_BUFFER_PCT", 0.0))
DAILY_LOSS_LIMIT_PCT = env_float("DAILY_LOSS_LIMIT_PCT", 4.0)
DAILY_PROFIT_TARGET_PCT = env_float("DAILY_PROFIT_TARGET_PCT", 6.0)
INCLUDE_FLOATING_IN_LOSS_LIMIT = True
MAX_SAME_DIRECTION = int(env_float("MAX_SAME_DIRECTION", 3))
MAX_FUNDING_RATE = 0.00045
MAX_ENTRY_SLIPPAGE_PCT = env_float("MAX_ENTRY_SLIPPAGE_PCT", 0.004)
USE_BTC_FILTER = True

# ---------- AI Gateway & Local AI ----------
AI_GATEWAY_URL = os.getenv("AI_GATEWAY_URL", "https://api.openai.com/v1/chat/completions")
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")
ENABLE_EXTERNAL_AI = env_flag("ENABLE_EXTERNAL_AI", False)
ENABLE_LOCAL_AI = env_flag("ENABLE_LOCAL_AI", False)
USE_AI_ENTRY_FILTER = env_flag("USE_AI_ENTRY_FILTER", False)
BACKUP_INTERVAL_SEC = int(env_float("BACKUP_INTERVAL_SEC", 6 * 3600))
AI_MIN_PROB = env_float("AI_MIN_PROB", 0.50)
AI_MIN_PROB_LONG = env_float("AI_MIN_PROB_LONG", AI_MIN_PROB)
AI_MIN_PROB_SHORT = env_float("AI_MIN_PROB_SHORT", AI_MIN_PROB)
VERBOSE_REJECT_LOG = env_flag("VERBOSE_REJECT_LOG", True)

# ---------- Paper Mode ----------
ENABLE_PAPER_LOG = env_flag("ENABLE_PAPER_LOG", True)

# ---------- بروفايلات الاستراتيجية ----------
PROFILES = {
    "conservative": {
        "min_atr_pct": 0.004, "min_adx": 23, "min_vol_mult": 1.2,
        "regime_adx4_strong": 28, "regime_adx1_strong": 24, "regime_adx4_weak": 20,
        "rsi_long_min": 40, "rsi_long_max": 61, "rsi_short_min": 39, "rsi_short_max": 60,
        "near_ema21_pct": 0.011, "near_ema50_pct": 0.016, "max_spread_pct": 0.0011,
        "rel_atr_ratio": 99, "atr_hard_floor": 0.003,
        "bo_vol_mult": 1.8, "bo_max_ext_atr": 2.0,
    },
    "balanced": {
        "min_atr_pct": 0.003, "min_adx": 20, "min_vol_mult": 1.0,
        "regime_adx4_strong": 26, "regime_adx1_strong": 22, "regime_adx4_weak": 18,
        "rsi_long_min": 38, "rsi_long_max": 64, "rsi_short_min": 36, "rsi_short_max": 62,
        "near_ema21_pct": 0.013, "near_ema50_pct": 0.019, "max_spread_pct": 0.0015,
        "rel_atr_ratio": 1.15, "atr_hard_floor": 0.0018,
        "bo_vol_mult": 1.5, "bo_max_ext_atr": 2.5,
    },
    "opportunist": {
        "min_atr_pct": 0.0026, "min_adx": 19, "min_vol_mult": 0.9,
        "regime_adx4_strong": 25, "regime_adx1_strong": 21, "regime_adx4_weak": 16,
        "rsi_long_min": 36, "rsi_long_max": 66, "rsi_short_min": 34, "rsi_short_max": 64,
        "near_ema21_pct": 0.014, "near_ema50_pct": 0.021, "max_spread_pct": 0.0018,
        "rel_atr_ratio": 1.05, "atr_hard_floor": 0.0017,
        "bo_vol_mult": 1.3, "bo_max_ext_atr": 2.8,
    },
    "active": {
        "min_atr_pct": 0.0022, "min_adx": 18, "min_vol_mult": 0.8,
        "regime_adx4_strong": 24, "regime_adx1_strong": 20, "regime_adx4_weak": 15,
        "rsi_long_min": 35, "rsi_long_max": 67, "rsi_short_min": 33, "rsi_short_max": 65,
        "near_ema21_pct": 0.016, "near_ema50_pct": 0.024, "max_spread_pct": 0.003,
        "rel_atr_ratio": 0.9, "atr_hard_floor": 0.0015,
        "bo_vol_mult": 1.2, "bo_max_ext_atr": 3.0,
    },
}

_default_profile = "active" if USE_SANDBOX else "opportunist"
PROFILE_NAME = os.getenv("PROFILE", _default_profile).strip().lower()
PROFILE_WARNING = ""
if PROFILE_NAME not in PROFILES:
    PROFILE_WARNING = f"بروفايل غير معروف '{PROFILE_NAME}' — استُخدم {_default_profile}"
    PROFILE_NAME = _default_profile
if not USE_SANDBOX and PROFILE_NAME == "active":
    PROFILE_WARNING = "البروفايل active ممنوع على الحقيقي — استُخدم opportunist"
    PROFILE_NAME = "opportunist"

ENABLE_SNIPER = env_flag("ENABLE_SNIPER", True)
SNIPER_ONLY = env_flag("SNIPER_ONLY", False)

SNIPER = {
    "label": "قناص",
    "tfs": ("5m", "15m", "1h"),
    "leverage": int(env_float("SNIPER_LEVERAGE", 5)),
    "risk_pct": env_float("SNIPER_RISK_PCT", 0.32),
    "min_score": int(env_float("SNIPER_MIN_SCORE", 83)),
    "atr_sl_mult": 1.18,
    "rr": 1.85,
    "min_sl": 0.0048,
    "max_sl": 0.0145,
    "trail_atr": 0.68,
    "be_r": 0.48,
    "p1_r": 0.80,
    "p1_pct": 0.55,
    "p2_r": 1.40,
    "p2_pct": 0.45,
    "max_hold_minutes": int(env_float("SNIPER_MAX_HOLD_MIN", 75)),
    "cooldown": 420,
    "max_open": 2,
    "max_entry_slip": 0.0018,
    "max_signal_age_sec": 110,
    "min_ker": 0.29,
    "min_vol_mult": 1.40,
    "near_ema_pct": 0.0085,
}

def _cfg(key):
    return env_float(key.upper(), PROFILES[PROFILE_NAME][key])

MIN_ATR_PCT = _cfg("min_atr_pct")
MAX_ATR_PCT = 0.045
MIN_ADX = MIN_ADX_OVERRIDE
MIN_VOL_MULT = _cfg("min_vol_mult")
REGIME_ADX4_STRONG = _cfg("regime_adx4_strong")
REGIME_ADX1_STRONG = _cfg("regime_adx1_strong")
REGIME_ADX4_WEAK = _cfg("regime_adx4_weak")
RSI_LONG_MIN = _cfg("rsi_long_min")
RSI_LONG_MAX = _cfg("rsi_long_max")
RSI_SHORT_MIN = _cfg("rsi_short_min")
RSI_SHORT_MAX = _cfg("rsi_short_max")
NEAR_EMA21_PCT = _cfg("near_ema21_pct")
NEAR_EMA50_PCT = _cfg("near_ema50_pct")
MAX_SPREAD_PCT = _cfg("max_spread_pct")
REL_ATR_RATIO = _cfg("rel_atr_ratio")
ATR_HARD_FLOOR = _cfg("atr_hard_floor")

ENABLE_BREAKOUT = env_flag("ENABLE_BREAKOUT", USE_SANDBOX)
BO_LOOKBACK = int(env_float("BO_LOOKBACK", 20))
BO_VOL_MULT = env_float("BO_VOL_MULT", _entry_defaults["bo_vol"])
BO_MAX_EXT_ATR = _cfg("bo_max_ext_atr")
BO_BODY_POS = env_float("BO_BODY_POS", 0.6)
BO_RSI_LONG_MIN = env_float("BO_RSI_LONG_MIN", 52)
BO_RSI_LONG_MAX = env_float("BO_RSI_LONG_MAX", 72)
BO_RSI_SHORT_MIN = env_float("BO_RSI_SHORT_MIN", 28)
BO_RSI_SHORT_MAX = env_float("BO_RSI_SHORT_MAX", 48)

ENABLE_KER_FILTER = env_flag("ENABLE_KER_FILTER", True)

WATCHDOG_SEC = 300
TF_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}

DATA_DIR = os.getenv("BOT_DATA_DIR") or (
    "/data" if os.path.isdir("/data") and os.access("/data", os.W_OK) else ".")
STATE_FILE = os.path.join(DATA_DIR, "bot_state.json")
JOURNAL_FILE = os.path.join(DATA_DIR, "trades_journal.csv")
PAPER_FILE = os.path.join(DATA_DIR, "paper_signals.csv")
LOCK_FILE = os.path.join(DATA_DIR, "bot.lock")
