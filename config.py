import os
import math
from datetime import timezone, timedelta

KSA_TZ = timezone(timedelta(hours=3))


def env_float(name, default):
    try:
        v = float(os.getenv(name, default))
        return v if math.isfinite(v) else float(default)
    except ValueError:
        return float(default)


def env_flag(name, default):
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes")


# ============== Exchange ==============
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET = os.getenv("BINANCE_SECRET", "")
USE_SANDBOX = env_flag("USE_SANDBOX", True)
ALLOW_LIVE_TRADING = env_flag("ALLOW_LIVE_TRADING", False)

# ============== Trading ==============
LEVERAGE = int(env_float("LEVERAGE", 5))
MARGIN_MODE = "isolated"
MAX_OPEN_POSITIONS = int(env_float("MAX_OPEN_POSITIONS", 6))
MAX_SAME_DIRECTION = int(env_float("MAX_SAME_DIRECTION", 3))
MAX_PER_SECTOR_SAME_DIR = 2

# ... إلخ (كل env vars)
