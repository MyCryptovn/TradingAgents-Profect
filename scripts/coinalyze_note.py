"""Dữ liệu phái sinh gộp nhiều sàn từ Coinalyze (API miễn phí). Chỉ đọc."""
import json
import os
import time
import urllib.parse
import urllib.request

BASE = "https://api.coinalyze.net/v1/"
RENAME = {"XBT": "BTC", "XDG": "DOGE"}
_CACHE = {}


def _get(path, params):
    key = os.getenv("COINALYZE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("thiếu COINALYZE_API_KEY")
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"api_key": key, "User-Agent": "TradingAgents-Profect/coinalyze"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    time.sleep(0.4)
    return data


def _build(coin):
    sym = f"{coin}USDT_PERP.A"
    now_ts = int(time.time())
    oi = _get("open-interest", {"symbols": sym, "convert_to_usd": "true"})
    if not oi:
        return ""
    oi_now = float(oi[0]["value"])
    oh = _get("open-interest-history", {"symbols": sym, "interval": "1hour",
                                        "from": now_ts - 26 * 3600, "to": now_ts, "convert_to_usd": "true"})
    hist = oh[0]["history"] if oh else []
    fr = _get("funding-rate", {"symbols": sym})
    fund = float(fr[0]["value"]) if fr else 0.0
    ov = _get("ohlcv-history", {"symbols": sym, "interval": "5min", "from": now_ts - 3600, "to": now_ts})
    cand = ov[0]["history"] if ov else []

    def share(rows):
        v = sum(float(c["v"]) for c in rows)
        b = sum(float(c["bv"]) for c in rows)
        return b / v if v > 0 else 0.0

    lq = _get("liquidation-history", {"symbols": sym, "interval": "1hour",
                                      "from": now_ts - 4 * 3600, "to": now_ts, "convert_to_usd": "true"})
    lh = lq[0]["history"] if lq else []
    long_liq = sum(float(c["l"]) for c in lh)
    short_liq = sum(float(c["s"]) for c in lh)

    def chg(n):
        if len(hist) >= n and float(hist[-n]["c"]) > 0:
            return oi_now / float(hist[-n]["c"]) - 1
        return None

    text = f" COINALYZE AGGREGATED PERP DATA ({sym}, multi-exchange): open interest ${oi_now:,.0f}"
    for label, n in (("4h", 5), ("24h", 25)):
        c = chg(n)
        if c is not None:
            text += f", OI change {label} {c:+.1%}"
    text += f", funding {fund:+.4f}"
    if cand:
        text += f", taker buy share {share(cand[-6:]):.0%} (last 30m) and {share(cand):.0%} (last 60m)"
    text += f", liquidations last 4h: longs ${long_liq:,.0f} vs shorts ${short_liq:,.0f}."
    text += " Perpetual-futures data only; use as context, not as a standalone signal."
    return text


def coinalyze_note(ticker):
    base = ticker.split("-")[0].upper()
    coin = RENAME.get(base, base)
    if coin in _CACHE:
        return _CACHE[coin]
    note = ""
    try:
        note = _build(coin)
    except Exception as ex:
        print("coinalyze_note lỗi:", ex)
    _CACHE[coin] = note
    return note


if __name__ == "__main__":
    for t in ("BTC-USD", "TIA-USD", "XMR-USD", "PEPE-USD"):
        print(t, "->", coinalyze_note(t) or "(không có dữ liệu hoặc lỗi)")
