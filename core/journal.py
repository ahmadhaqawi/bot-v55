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
