# backup.py — النسخ الاحتياطي والاستعادة عبر تيليجرام
import os
import io
import re
import csv
import json
import math
import time
import shutil
import logging
from datetime import datetime, timezone
import requests

from config import (
    AUTHORIZED_CHATS, JOURNAL_FILE, PAPER_FILE, STATE_FILE, TELEGRAM_BOT_TOKEN,
)
from core.state import (
    _atomic_write, load_state, meta, new_sym_state,
    open_positions_snapshot, state_lock, trade_lock,
)
from core.journal import JOURNAL_FIELDS, PAPER_FIELDS, journal_lock
from telegram.notifier import send

log = logging.getLogger("bot")
RESTORE_LIMIT = 10 * 1024**2


def backup_to_telegram(chat_id=None):
    if not TELEGRAM_BOT_TOKEN or not AUTHORIZED_CHATS:
        return
    targets = [chat_id] if chat_id else list(AUTHORIZED_CHATS)
    for cid in targets:
        for filepath in [JOURNAL_FILE, STATE_FILE, PAPER_FILE]:
            file_name = os.path.basename(filepath)
            if os.path.exists(filepath):
                try:
                    lock = state_lock if filepath == STATE_FILE else journal_lock
                    with lock:
                        with open(filepath, "rb") as f:
                            file_data = f.read()
                    r = requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument",
                        data={"chat_id": cid, "caption": f"🔒 {file_name}"},
                        files={"document": (file_name, file_data)},
                        timeout=30,
                    )
                    if not r.ok:
                        log.error(f"[BACKUP] TG Response: {r.text}")
                except Exception as e:
                    log.error(f"[BACKUP] فشل إرسال {filepath}: {e}")
                    send(f"❌ فشل إرسال {file_name}: {e}", cid)
            else:
                send(f"⚠️ الملف <code>{file_name}</code> غير موجود في السيرفر حالياً.", cid)


def _validated_restore(file_name, raw):
    text = raw.decode("utf-8-sig")
    if file_name == "bot_state.json":
        data = json.loads(text)
        if not isinstance(data, dict) or not all(
            isinstance(data.get(k), dict) for k in ("state", "daily_pnl", "meta")
        ):
            raise ValueError("صيغة حالة غير صالحة")
        flat = {}
        for sym, row in data["state"].items():
            if (not re.fullmatch(r"[A-Za-z0-9_.-]+/USDT:USDT", sym)
                    or not isinstance(row, dict) or row.get("position") is not None):
                raise ValueError("نسخة الحالة يجب ألا تحتوي على مراكز مفتوحة")
            last_exit = row.get("last_exit_ts", 0)
            if type(last_exit) not in (int, float) or not math.isfinite(last_exit) or last_exit < 0:
                raise ValueError("وقت إغلاق غير صالح")
            flat[sym] = {**new_sym_state(), "last_exit_ts": last_exit,
                         "last_exit_mode": row.get("last_exit_mode", "swing")}
        day = data["daily_pnl"]
        value, trades, wins = day["value"], day["trades"], day["wins"]
        datetime.strptime(day["date"], "%Y-%m-%d")
        if (type(value) not in (int, float) or not math.isfinite(value) or type(trades) is not int
                or type(wins) is not int or not 0 <= wins <= trades):
            raise ValueError("قيم الإحصاء اليومي غير صالحة")
        safe_day = {"value": value, "trades": trades, "wins": wins, "date": day["date"]}
        return json.dumps({"state": flat, "daily_pnl": safe_day, "meta": {"paused": True}},
                          ensure_ascii=False, allow_nan=False).encode("utf-8")

    fields = JOURNAL_FIELDS if file_name == "trades_journal.csv" else PAPER_FIELDS
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    if reader.fieldnames != fields:
        raise ValueError("رأس CSV لا يطابق بنية الإصدار الحالي")
    numeric = ("entry", "exit", "pnl", "r_multiple") if fields is JOURNAL_FIELDS else ("score", "price", "sl", "tp")
    for row in reader:
        if (None in row or None in row.values() or row["side"] not in ("LONG", "SHORT")
                or not row["symbol"] or not all(math.isfinite(float(row[k])) for k in numeric)):
            raise ValueError("صف CSV غير صالح")
    return text.encode("utf-8")


def handle_document_restore(msg, chat_id, ex):
    doc = msg.get("document")
    if not doc or chat_id not in AUTHORIZED_CHATS:
        return
    if (msg.get("chat") or {}).get("type") != "private":
        return send("الاستعادة متاحة في محادثة خاصة مصرح بها فقط.", chat_id)
    if (msg.get("caption") or "").strip().lower() != "/restore confirm":
        return send("أضف /restore confirm في تعليق الملف إذا أردت استبدال النسخة الحالية.", chat_id)
    file_name = doc.get("file_name", "")
    if file_name not in ("trades_journal.csv", "bot_state.json", "paper_signals.csv"):
        return send("❌ الملف غير معروف.", chat_id)
    try:
        if doc.get("file_size", 0) > RESTORE_LIMIT:
            raise ValueError("restore file too large")
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile",
                         params={"file_id": doc["file_id"]}, timeout=(5, 15), allow_redirects=False)
        r.raise_for_status()
        file_path = r.json()["result"]["file_path"]
        if not re.fullmatch(r"[A-Za-z0-9_/-]+(?:\.[A-Za-z0-9]+)?", file_path):
            raise ValueError("invalid remote file path")
        dl_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
        target = {"trades_journal.csv": JOURNAL_FILE,
                  "bot_state.json": STATE_FILE,
                  "paper_signals.csv": PAPER_FILE}[file_name]
        raw = bytearray()
        with requests.get(dl_url, stream=True, timeout=(5, 15), allow_redirects=False) as r_dl:
            r_dl.raise_for_status()
            deadline = time.monotonic() + 60
            for chunk in r_dl.iter_content(chunk_size=8192):
                raw.extend(chunk)
                if len(raw) > RESTORE_LIMIT or time.monotonic() > deadline:
                    raise ValueError("restore download exceeded its budget")
        payload = _validated_restore(file_name, raw)
        with trade_lock:
            if not meta.get("paused") or open_positions_snapshot():
                return send("أوقف الدخول بـ /pause وأغلق المراكز قبل الاستعادة.", chat_id)
            if any(abs(float(p.get("contracts") or 0)) > 0 for p in ex.fetch_positions()):
                return send("توجد مراكز مفتوحة على Binance. أوقف الاستعادة حتى تُغلق.", chat_id)
            with (state_lock if file_name == "bot_state.json" else journal_lock):
                if os.path.exists(target):
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                    shutil.copy2(target, f"{target}.{stamp}.bak")
                _atomic_write(target, payload)
                if file_name == "bot_state.json":
                    load_state()
        send(f"✅ تم استعادة {file_name}. لا يزال فتح الصفقات متوقفًا حتى /resume.", chat_id)
    except Exception as e:
        log.warning("[RESTORE] %s: %s", file_name, type(e).__name__)
        send("❌ تعذرت الاستعادة. تأكد من صيغة الملف وحجمه وحالة الاتصال.", chat_id)
