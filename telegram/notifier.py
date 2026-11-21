# telegram/notifier.py — إرسال غير محجوب عبر queue + إعادة تصدير دوال الرسائل
import logging
import queue
import threading
import requests

from config import TELEGRAM_BOT_TOKEN, AUTHORIZED_CHATS
from telegram.constants import STRAT_LABEL, REASONS
from telegram.messages import short, fmt, msg_entry, msg_exit

log = logging.getLogger("bot")

_send_queue = queue.Queue(maxsize=1000)


def send(msg, chat_id=None):
    """غير محجوب: يضع الرسالة في queue ويعود فوراً."""
    try:
        _send_queue.put_nowait((msg, chat_id))
    except queue.Full:
        log.warning("[TG QUEUE] full — dropped")


def _send_worker():
    """Thread منفصل يرسل الرسائل من الـ queue."""
    while True:
        try:
            msg, chat_id = _send_queue.get()
            if not TELEGRAM_BOT_TOKEN:
                continue
            targets = [chat_id] if chat_id else list(AUTHORIZED_CHATS)
            for cid in targets:
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                        json={"chat_id": cid, "text": msg, "parse_mode": "HTML"},
                        timeout=10,
                    )
                except Exception as e:
                    log.warning(f"[TG SEND] {e}")
        except Exception as e:
            log.error(f"[TG WORKER] {e}")


def start_sender():
    """يشغّل thread الإرسال في الخلفية."""
    threading.Thread(target=_send_worker, daemon=True).start()
