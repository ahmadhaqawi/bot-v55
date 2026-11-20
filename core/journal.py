# core/journal.py — سجل الصفقات (CSV)، سجل إشارات Paper Mode، ونصوص الإحصائيات
import os
import csv
import logging
import threading
from datetime import datetime

import pandas as pd

from config import JOURNAL_FILE, PAPER_FILE, VERSION, ENABLE_PAPER_LOG, KSA_TZ
from telegram.notifier import short, STRAT_LABEL

log = logging.getLogger("bot")

journal_lock = threading.Lock()


JOURNAL_FIELDS = [
    "close_time", "symbol", "side", "entry", "exit", "initial_contracts",
    "pnl", "r_multiple", "reason", "regime", "duration_min", "strategy",
    "fees", "fees_source", "stop_slip_usd", "stop_slip_r", "entry_slip_usd",
    "risk_usd", "score", "f_trend", "f_volume", "f_adx", "f_momentum",
    "f_candle", "f_setup", "f_entry_quality", "volume_ratio", "ker",
]


PAPER_FIELDS = ["ts", "symbol", "side", "strategy", "score", "price", "sl", "tp", "blocked_by"]


def migrate_journal():
    try:
        with journal_lock:
            if not os.path.exists(JOURNAL_FILE):
                return
            with open(JOURNAL_FILE, newline="", encoding="utf-8") as f:
                header = [h.strip() for h in f.readline().strip().split(",")]
            if header == JOURNAL_FIELDS:
                return
            with open(JOURNAL_FILE, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            for r in rows:
                if not r.get("strategy"):
                    r["strategy"] = "legacy"
            with open(JOURNAL_FILE, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=JOURNAL_FIELDS, extrasaction="ignore", restval="")
                w.writeheader()
                w.writerows(rows)
            log.info(f"[JOURNAL] تم ترحيل {len(rows)} صفقة إلى صيغة {VERSION}")
    except Exception as e:
        log.error(f"[JOURNAL MIGRATE] {e}")


def journal_append(row):
    try:
        with journal_lock:
            new = not os.path.exists(JOURNAL_FILE)
            with open(JOURNAL_FILE, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=JOURNAL_FIELDS)
                if new:
                    w.writeheader()
                w.writerow(row)
    except Exception as e:
        log.error(f"[JOURNAL] {e}")


def log_paper_signal(sym, sig, blocked_by):
    if not ENABLE_PAPER_LOG:
        return
    try:
        with journal_lock:
            new = not os.path.exists(PAPER_FILE)
            with open(PAPER_FILE, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=PAPER_FIELDS)
                if new:
                    w.writeheader()
                w.writerow({
                    "ts": datetime.now(KSA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol": short(sym), "side": sig["side"],
                    "strategy": sig["strategy"], "score": sig.get("score", 0),
                    "price": round(sig["price"], 6),
                    "sl": round(sig["sl"], 6), "tp": round(sig["tp"], 6),
                    "blocked_by": blocked_by,
                })
    except Exception as e:
        log.warning(f"[PAPER] {e}")


def paper_stats_text():
    if not os.path.exists(PAPER_FILE):
        return "📭 لا توجد إشارات مرشحة بعد."
    try:
        with journal_lock:
            df = pd.read_csv(PAPER_FILE)
    except Exception as e:
        return f"خطأ: {e}"
    if df.empty:
        return "📭 لا توجد إشارات مرشحة بعد."
    n = len(df)
    by_block = df["blocked_by"].value_counts().head(8)
    lines = [f"📄 <b>إشارات Paper Mode</b> — {n} إشارة", "━━━━━━━━━━━━━━━",
             "<b>حسب سبب الحجب:</b>"]
    for reason, cnt in by_block.items():
        lines.append(f"• {reason}: {cnt}")
    try:
        bins = pd.cut(df["score"], bins=[0, 50, 60, 65, 70, 75, 80, 100])
        lines += ["", "<b>توزيع الدرجات:</b>"]
        for interval, cnt in df.groupby(bins, observed=False).size().items():
            if cnt:
                lines.append(f"• {interval}: {cnt}")
    except Exception:
        pass
    lines.append("\n💡 PAPER_FILE على /data للتحليل المفصّل")
    return "\n".join(lines)


def _group_line(name, g):
    wins = g[g["pnl"] > 0]
    wr = len(wins) / len(g) * 100
    avg_r = g["r_multiple"].mean() if "r_multiple" in g else 0.0
    return f"• {name}: {len(g)} صفقة | ربح {wr:.0f}% | صافي {g['pnl'].sum():+.2f} | R {avg_r:+.2f}"


def _num(df, col):
    if col not in df:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def stats_text():
    try:
        with journal_lock:
            if not os.path.exists(JOURNAL_FILE):
                return "📭 لا يوجد سجل صفقات بعد."
            df = pd.read_csv(JOURNAL_FILE)
    except Exception as e:
        return f"خطأ في قراءة السجل: {e}"
    if df.empty:
        return "📭 لا يوجد سجل صفقات بعد."
    if "strategy" not in df:
        df["strategy"] = "legacy"
    df["strategy"] = df["strategy"].fillna("legacy")
    df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce").fillna(0.0)
    df["r_multiple"] = _num(df, "r_multiple")
    n = len(df)
    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    gross_win = wins["pnl"].sum()
    gross_loss = abs(losses["pnl"].sum())
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    curve = df["pnl"].cumsum()
    max_dd = (curve.cummax() - curve).max()
    avg_r = df["r_multiple"].mean()
    wr = len(wins) / n * 100
    avg_win = wins["pnl"].mean() if len(wins) else 0.0
    avg_loss = losses["pnl"].mean() if len(losses) else 0.0
    pf_txt = "∞" if pf == float("inf") else f"{pf:.2f}"
    lines = [f"📈 <b>إحصائيات الأداء</b>", "━━━━━━━━━━━━━━━",
             f"🔢 الصفقات: {n}", f"✅ نسبة الربح: {wr:.1f}%",
             f"💰 الصافي: <b>{df['pnl'].sum():+.2f} USDT</b>",
             f"⚖️ PF: {pf_txt}", f"📐 متوسط R: {avg_r:+.2f}",
             f"🟢 متوسط الربح: {avg_win:+.2f} | 🔴 متوسط الخسارة: {avg_loss:+.2f}",
             f"📉 أقصى تراجع: {max_dd:.2f} USDT", "", "<b>حسب الاستراتيجية:</b>"]
    for name, g in df.groupby("strategy"):
        lines.append(_group_line(STRAT_LABEL.get(name, name), g))
    if "side" in df:
        lines += ["", "<b>حسب الاتجاه:</b>"]
        for name, g in df.groupby("side"):
            lines.append(_group_line(name, g))
    score = _num(df, "score")
    if score.notna().any():
        lines += ["", "<b>حسب درجة الفرصة:</b>"]
        for lo, hi, name in ((60, 67, "60–67"), (68, 73, "68–73"),
                             (74, 79, "74–79"), (80, 89, "80–89"), (90, 100, "90+")):
            g = df[(score >= lo) & (score <= hi)]
            if len(g):
                lines.append(_group_line(name, g))
    fees = _num(df, "fees").dropna()
    stop_usd = _num(df, "stop_slip_usd").dropna()
    stop_r = _num(df, "stop_slip_r").dropna()
    entry_slip = _num(df, "entry_slip_usd").dropna()
    n_actual = int((df["fees_source"] == "actual").sum()) if "fees_source" in df else 0
    if len(fees) or len(stop_usd) or len(entry_slip):
        lines += ["", "<b>تكلفة التنفيذ:</b>"]
        if len(fees):
            lines.append(f"🧾 الرسوم: {fees.sum():.2f} USDT ({n_actual} فعلية)")
        if len(stop_usd):
            lines.append(f"⚡ انزلاق الوقف: متوسط {stop_usd.mean():.2f} ({stop_r.mean():.2f}R)")
        if len(entry_slip):
            lines.append(f"↘️ انزلاق الدخول: إجمالي {entry_slip.sum():.2f}")
    if n < 30:
        lines += ["", f"⚠️ العينة صغيرة ({n}) — لا تعتمد قبل 30 صفقة."]
    lines.append("━━━━━━━━━━━━━━━")
    return "\n".join(lines)
