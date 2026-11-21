from datetime import timezone, timedelta

KSA_TZ = timezone(timedelta(hours=3))

STRAT_LABEL = {
    "pullback": "الارتداد",
    "breakout": "الاختراق",
    "adopted": "متبنّاة",
    "legacy": "قديمة"
}

REASONS = {
    "Take Profit": "تحقق الهدف 🎯",
    "Stop Loss": "ضرب وقف الخسارة 🛑",
    "Breakeven Hit": "خروج آمن 🛡️",
    "Trailing Stop": "وقف متحرك 📈",
    "Manual": "إغلاق يدوي 🖐️",
    "Emergency": "إغلاق طارئ 🚨",
    "Manual/External": "إغلاق من المنصة ⚡",
    "Time Exit": "انتهاء مدة القناص ⏱️",
    "Unconfirmed Exit": "سبب الإغلاق غير مؤكد",
    "Estimated Take Profit": "تحقق الهدف (تقديري)",
    "Estimated Stop Loss": "وقف الخسارة (تقديري)",
    "Estimated Trailing Stop": "وقف متحرك (تقديري)",
    "Estimated Breakeven Hit": "تعادل (تقديري)",
}
