# Telegram Listener

# telegram/listener.py — استقبال رسائل تيليجرام (long polling) وتوجيهها
import time
import logging
import requests

from config import AUTHORIZED_CHATS, TELEGRAM_BOT_TOKEN
from exchange import init_exchange
from telegram.notifier import send
from telegram.commands import handle_command
from backup import handle_document_restore

log = logging.getLogger("bot")


def _reply_blocked_symbols(chat_id):
    try:
        import sys
        main_mod = sys.modules.get("main")
        if main_mod is None or not hasattr(main_mod, "get_blocked_symbols_summary"):
            import main as main_mod
        summary = main_mod.get_blocked_symbols_summary()
    except Exception as e:
        log.warning(f"[TG BLOCKED SUMMARY] {e}")
        summary = "⚠️ تعذر جلب قائمة العملات المحجوبة حالياً."
    send(summary, chat_id)


def telegram_listener(active_symbols):
    if not TELEGRAM_BOT_TOKEN or not AUTHORIZED_CHATS:
        return
    try:
        ex2 = init_exchange()
    except Exception as e:
        log.error(f"[TG INIT] {e}")
        return
    offset = None
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                             params={"timeout": 25, "offset": offset}, timeout=35).json()
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                text, chat_id = msg.get("text", ""), str(msg.get("chat", {}).get("id", ""))
                if chat_id not in AUTHORIZED_CHATS:
                    continue
                if "document" in msg:
                    handle_document_restore(msg, chat_id, ex2)
                    continue
                if not text.startswith("/"):
                    continue
                cmd = text.strip().split()[0].split("@")[0].lower()
                if cmd in ("/blocked", "/حجب"):
                    _reply_blocked_symbols(chat_id)
                    continue
                try:
                    handle_command(ex2, text, chat_id, active_symbols)
                except Exception as e:
                    log.exception(f"[TG CMD] {text[:80]}")
                    send("حدث خطأ داخلي. راجع سجل البوت.", chat_id)
        except Exception as e:
            log.warning("[TG POLL] %s", type(e).__name__)
            time.sleep(5)
