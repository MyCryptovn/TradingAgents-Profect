"""Chỉ báo nến đa khung (1m,5m,15m,1h,4h) cho lời nhắc của hội đồng."""
import json
import urllib.parse
import urllib.request

API = "https://api.kraken.com/0/public/"
API = "https://api.hyperliquid.xyz/info"

def _ohlc(pair, interval):
    url = API + "OHLC?" + urllib.parse.urlencode({"pair": pair, "interval": interval})
    with urllib.request.urlopen(url, timeout=30) as r:
        d = json.load(r)
    if d.get("error"):
        raise RuntimeError(d["error"])
    res = d["result"]
    rows = res[next(k for k in res if k != "last")]
    return rows[:-1] if len(rows) > 1 else []


def _ema(values, n):
    k = 2 / (n + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def _rsi(closes, n=14):
    if len(closes) < n + 1:
        return 50.0
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(len(closes) - n, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(len(closes) - n, len(closes))]
    ag, al = sum(gains) / n, sum(losses) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def _block(rows, label):
    closes = [float(c[4]) for c in rows]
    vols = [float(c[6]) for c in rows]
    colors = "".join("G" if float(c[4]) >= float(c[1]) else "R" for c in rows[-5:])
    ema9 = _ema(closes[-40:], 9)
    ema21 = _ema(closes[-60:] if len(closes) >= 60 else closes, 21)
    vr = (sum(vols[-3:]) / 3) / max(sum(vols[-15:-3]) / 12, 1e-12)
    ch = closes[-1] / closes[-7] - 1
    return (f" {label}: last close {closes[-1]:.6g}, EMA9 {ema9:.6g}, EMA21 {ema21:.6g}, "
            f"RSI14 {_rsi(closes):.0f}, last 5 candles {colors} (G green, R red), "
            f"volume of last 3 candles is {vr:.1f}x the prior average, change over last 6 candles {ch:+.2%}.")


def candle_note(ticker):
    pair = ticker.replace("-", "")
    if pair.startswith("BTC"):
        pair = "XBT" + pair[3:]
    try:
        r1 = _ohlc(pair, 1)
        r5 = _ohlc(pair, 5)
        r15 = _ohlc(pair, 15)
        r60 = _ohlc(pair, 60)
        r4h = _ohlc(pair, 240)
        if len(r1) < 60 or len(r5) < 60:
            return ""
        note = (" REAL-TIME CANDLES (Kraken, completed candles only):"
                + _block(r1, "1-minute")
                + _block(r5, "5-minute")
                + _block(r15, "15-minute")
                + _block(r60, "1-hour")
                + _block(r4h, "4-hour")
                + " Use these as timing confirmation: do not BUY if the last 3 one-minute candles"
                " are all red and RSI14 is below 45; do not SELL if they are all green and RSI14 is above 55."
                " Prefer setups where 15m and 1h and 4h trends agree with the scanner.")
        return note
    except Exception:
        return ""
