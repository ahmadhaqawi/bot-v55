import os
import csv

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
                w = csv.DictWriter(
                    f,
                    fieldnames=JOURNAL_FIELDS,
                    extrasaction="ignore",
                    restval=""
                )
                w.writeheader()
                w.writerows(rows)

            log.info(
                f"[JOURNAL] تم ترحيل {len(rows)} صفقة إلى صيغة {VERSION}"
            )

    except Exception as e:
        log.error(f"[JOURNAL MIGRATE] {e}")

def journal_append(row):
    try:
        with journal_lock:
            new = not os.path.exists(JOURNAL_FILE)

            with open(
                JOURNAL_FILE,
                "a",
                newline="",
                encoding="utf-8"
            ) as f:

                w = csv.DictWriter(
                    f,
                    fieldnames=JOURNAL_FIELDS
                )

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
