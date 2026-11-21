
def short(sym):
    try:
        return sym.split("/")[0]
    except Exception:
        return str(sym)


def fmt(value, digits=2):
    try:
        return f"{float(value):,.{digits}f}"
    except Exception:
        return "-"
