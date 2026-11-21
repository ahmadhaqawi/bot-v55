# ai/local_ai.py — تحميل النموذج المحلي (SHA-256)، التنبؤ، وتحليل سجل الصفقات
import os
import io
import re
import time
import hashlib
import logging
import tempfile
from urllib.parse import urlsplit

import pandas as pd
import requests

from config import (
    AI_API_KEY, AI_GATEWAY_URL, AI_MODEL,
    ENABLE_EXTERNAL_AI, ENABLE_LOCAL_AI, JOURNAL_FILE,
)
from core.journal import journal_lock
from telegram.notifier import send

log = logging.getLogger("bot")

AI_IS_ACTIVE = False
ai_model = None
le_regime = None
le_strategy = None
le_side = None


def _verified_bytes(path, expected, size_limit):
    with open(path, "rb") as f:
        payload = f.read(size_limit + 1)
    if len(payload) > size_limit or hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError(f"حجم أو بصمة غير صالحة: {os.path.basename(path)}")
    return payload


def _download_model(url, path, expected, size_limit):
    if urlsplit(url).scheme != "https":
        raise ValueError("AI_MODEL_URL must use HTTPS")
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(os.path.abspath(path)), delete=False) as tmp:
        staging = tmp.name
    try:
        with requests.get(url, timeout=(10, 60), stream=True) as response, open(staging, "wb") as out:
            response.raise_for_status()
            if any(urlsplit(item.url).scheme != "https" for item in [*response.history, response]):
                raise ValueError("AI model redirect must use HTTPS")
            size = 0
            deadline = time.monotonic() + 180
            for chunk in response.iter_content(128 * 1024):
                size += len(chunk)
                if size > size_limit or time.monotonic() > deadline:
                    raise ValueError("AI model download exceeded its size or time budget")
                out.write(chunk)
        _verified_bytes(staging, expected, size_limit)
        os.replace(staging, path)
    finally:
        if os.path.exists(staging):
            os.unlink(staging)


def load_local_ai():
    global AI_IS_ACTIVE, ai_model, le_regime, le_strategy, le_side
    AI_IS_ACTIVE = False
    ai_model = le_regime = le_strategy = le_side = None
    if not ENABLE_LOCAL_AI:
        return
    specs = (
        ("ai_model_mega_v4.pkl", "AI_MODEL_SHA256", 128 * 1024**2),
        ("le_regime_mega_v4.pkl", "AI_REGIME_SHA256", 4 * 1024**2),
        ("le_strategy_mega_v4.pkl", "AI_STRATEGY_SHA256", 4 * 1024**2),
        ("le_side_mega_v4.pkl", "AI_SIDE_SHA256", 4 * 1024**2),
    )
    try:
        import joblib
        folder = os.getenv("AI_MODEL_DIR", ".")
        pins = [re.sub(r"\s+", "", os.getenv(env, "")).lower() for _, env, _ in specs]
        for (_, env, _), pin in zip(specs, pins):
            if not re.fullmatch(r"[0-9a-f]{64}", pin):
                raise ValueError(f"بصمة غير صالحة أو ناقصة في المتغير: {env}")
        verified = []
        for (name, _, limit), pin in zip(specs, pins):
            path = os.path.join(folder, name)
            url = os.getenv("AI_MODEL_URL", "").strip() if name == specs[0][0] else ""
            if not os.path.exists(path) and url:
                log.info(f"[AI] جاري تنزيل {name} ...")
                _download_model(url, path, pin, limit)
            verified.append(_verified_bytes(path, pin, limit))
        ai_model, le_regime, le_strategy, le_side = [joblib.load(io.BytesIO(blob)) for blob in verified]
        AI_IS_ACTIVE = True
        log.info("[AI] تم تحميل النموذج المحلي من ملفات مطابقة للبصمات")
    except Exception as e:
        log.warning(f"[AI] لم يُحمّل النموذج المحلي: {type(e).__name__} - {e}")


def predict_signal(signal):
    if not AI_IS_ACTIVE or ai_model is None:
        return None
    factors = signal.get("score_factors", {})
    features = [
        signal.get("score", 0), factors.get("trend", 0), factors.get("volume", 0),
        factors.get("adx", 0), factors.get("momentum", 0), factors.get("candle", 0),
        factors.get("setup", 0), factors.get("entry_quality", 0),
        signal.get("volume_ratio") or 0, signal.get("ker") or 0,
        le_regime.transform([signal.get("regime", "unknown")])[0],
        le_strategy.transform([signal.get("strategy", "pullback")])[0],
        le_side.transform([signal.get("side", "LONG")])[0],
    ]
    prob = float(ai_model.predict_proba([features])[0][1])
    if not 0 <= prob <= 1:
        raise ValueError("invalid model probability")
    signal["ai_probability"] = prob
    return prob


def local_ai_analysis(df):
    n = len(df)
    if n == 0:
        return "📭 لا يوجد سجل صفقات."
    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    wr = len(wins) / n * 100
    avg_win = wins["pnl"].mean() if len(wins) else 0
    avg_loss = losses["pnl"].mean() if len(losses) else 0
    best_pair = df.groupby("symbol")["pnl"].sum().idxmax()
    worst_pair = df.groupby("symbol")["pnl"].sum().idxmin()
    best_strat = df.groupby("strategy")["pnl"].sum().idxmax() if "strategy" in df else "—"
    lines = [
        "🧠 <b>تحليل محلي (بدون AI API):</b>",
        f"• نسبة الربح: {wr:.1f}%",
        f"• متوسط الربح: {avg_win:+.2f} | متوسط الخسارة: {avg_loss:+.2f}",
        f"• أفضل عملة: {best_pair} | أسوأ: {worst_pair}",
        f"• أفضل استراتيجية: {best_strat}",
    ]
    if avg_win > 0 and avg_loss < 0:
        rr = avg_win / abs(avg_loss)
        lines.append(f"• R/R: {rr:.2f}")
    return "\n".join(lines)


def ai_analyze_trades(chat_id):
    if not os.path.exists(JOURNAL_FILE):
        return send("📭 لا يوجد سجل للتحليل.", chat_id)
    try:
        with journal_lock:
            df = pd.read_csv(JOURNAL_FILE)
        if df.empty:
            return send("📭 السجل فارغ.", chat_id)
    except Exception as e:
        return send(f"خطأ: {e}", chat_id)
    send("⏳ جاري تحليل السجل...", chat_id)
    if not ENABLE_EXTERNAL_AI or not AI_API_KEY or urlsplit(AI_GATEWAY_URL).scheme != "https":
        return send(local_ai_analysis(df), chat_id)
    try:
        total_pnl = df["pnl"].sum()
        wr = (df["pnl"] > 0).mean() * 100
        recent = df.tail(15)[["symbol", "side", "strategy", "pnl", "r_multiple", "reason"]].to_csv(index=False)
        prompt = f"حلل سجل البوت ({len(df)} صفقة، WR {wr:.1f}%، صافي {total_pnl:.2f} USDT) باختصار بالعربية:\n{recent}"
        r = requests.post(
            AI_GATEWAY_URL,
            json={"model": AI_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3},
            headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
            timeout=45, allow_redirects=False,
        )
        r.raise_for_status()
        send(f"🧠 <b>تحليل AI:</b>\n\n{r.json()['choices'][0]['message']['content']}", chat_id)
    except Exception as e:
        log.error(f"[AI] {e}")
        send(f"⚠️ فشل AI: {e}\n\n{local_ai_analysis(df)}", chat_id)
