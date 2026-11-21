# risk/portfolio.py — حساب إجمالي مخاطرة المحفظة وحدود القطاعات والاتجاه
import math
from config import SECTORS, MAX_PER_SECTOR_SAME_DIR


def get_sector(sym):
    """إرجاع اسم القطاع للعملة (مثل BTC/USDT:USDT -> major)."""
    base = sym.split("/")[0].upper()
    for sector, coins in SECTORS.items():
        if base in coins:
            return sector
    return "other"


def portfolio_risk_pct(positions, equity):
    """حساب إجمالي نسبة المخاطرة المفتوحة حالياً في المحفظة (% من الرصيد)."""
    if not positions or not math.isfinite(equity) or equity <= 0:
        return 0.0
    total_risk_usd = 0.0
    for pos in positions.values():
        entry = float(pos.get("entry") or 0)
        sl = float(pos.get("sl") or 0)
        margin = float(pos.get("margin") or 0)
        lev = float(pos.get("leverage") or 1)
        if entry > 0 and sl > 0 and margin > 0 and lev > 0:
            sl_pct = abs(entry - sl) / entry
            total_risk_usd += margin * lev * sl_pct
    return (total_risk_usd / equity) * 100.0


def sector_limit_reached(positions, sym, side):
    """التحقق هل وصل القطاع للحد الأقصى في نفس الاتجاه."""
    sec = get_sector(sym)
    if sec == "other":
        return False
    count = sum(
        1 for s, p in positions.items()
        if p.get("position") == side and get_sector(s) == sec
    )
    return count >= MAX_PER_SECTOR_SAME_DIR
