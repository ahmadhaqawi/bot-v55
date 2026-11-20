def ema(s, p):
    return s.ewm(span=p, adjust=False).mean()


def rsi(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1 / p, adjust=False).mean()
    l = -d.clip(upper=0).ewm(alpha=1 / p, adjust=False).mean()
    return 100 - (100 / (1 + g / (l + 1e-9)))
