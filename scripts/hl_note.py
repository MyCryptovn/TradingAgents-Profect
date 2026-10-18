"""Ảnh chụp Hyperliquid nâng cấp: funding + OI + order book + nến đa khung."""
import json
import time
import urllib.request

URL = "https://api.hyperliquid.xyz/info"
RENAME = {"XBT": "BTC", "XDG": "DOGE"}


def _info(body):
    req = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _book(coin):
    bids, asks = _info({"type": "l2Book", "coin": coin})["levels"]
    return (
        [(float(x["px"]), float(x["sz"])) for x in bids],
        [(float(x["px"]), float(x["sz"])) for x in asks],
    )


def _depth(levels, mid, pct, below):
    lim = mid * (1 - pct) if below else mid * (1 + pct)
    return sum(p * s for p, s in levels if (p >= lim if below else p <= lim))


def _wall(levels, mid):
    p, s = max(levels, key=lambda x: x[0] * x[1])
    return p, s, abs(p / mid - 1)


def _left(levels, px):
    for p, s in levels:
        if p == px:
            return s
    return 0.0


def _candles(coin, interval, lookback_ms=6 * 3600 * 1000):
    """Lấy nến Hyperliquid (interval: 5m, 15m, 1h, 4h...)."""
    now = int(time.time() * 1000)
    body = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": now - lookback_ms,
            "endTime": now,
        },
    }
    rows = _info(body)
    if not isinstance(rows, list) or len(rows) < 10:
        return []
    return rows


def _candle_block(rows, label):
    if len(rows) < 10:
        return f" {label}: insufficient data."
    closes = [float(c["c"]) for c in rows]
    vols = [float(c["v"]) for c in rows]
    colors = "".join("G" if float(c["c"]) >= float(c["o"]) else "R" for c in rows[-5:])
    ch = closes[-1] / closes[-6] - 1 if len(closes) >= 6 else 0
    vr = (sum(vols[-3:]) / 3) / max(sum(vols[-12:-3]) / 9, 1e-12)
    trend = "up" if closes[-1] > closes[-5] else "down"
    return (
        f" {label}: last {closes[-1]:.6g}, trend {trend}, "
        f"last 5 candles {colors}, vol last3/prior {vr:.1f}x, "
        f"change \~6 candles {ch:+.2%}."
    )


def hl_note(ticker):
    coin = ticker.split("-")[0].upper()
    coin = RENAME.get(coin, coin)
    try:
        # --- Phần funding / OI / premium (giữ nguyên chất lượng cũ) ---
        meta, ctxs = _info({"type": "metaAndAssetCtxs"})
        idx = next((i for i, u in enumerate(meta["universe"]) if u["name"] == coin), None)
        if idx is None:
            return ""
        c = ctxs[idx]
        mark = float(c["markPx"])
        oi = float(c["openInterest"]) * mark
        vol = float(c["dayNtlVlm"])
        funding = float(c["funding"])
        prem = float(c["premium"] or 0)
        chg = mark / float(c["prevDayPx"]) - 1

        b1, a1 = _book(coin)
        mid = (b1[0][0] + a1[0][0]) / 2
        d5b = _depth(b1, mid, 0.005, True)
        d5a = _depth(a1, mid, 0.005, False)
        d1b = _depth(b1, mid, 0.01, True)
        d1a = _depth(a1, mid, 0.01, False)
        bw, aw = _wall(b1, mid), _wall(a1, mid)

        time.sleep(2.5)
        b2, a2 = _book(coin)
        bkeep = _left(b2, bw[0]) / bw[1] if bw[1] else 0
        akeep = _left(a2, aw[0]) / aw[1] if aw[1] else 0

        # --- Thêm nến đa khung từ Hyperliquid ---
        c5 = _candles(coin, "5m", 3 * 3600 * 1000)
        c15 = _candles(coin, "15m", 8 * 3600 * 1000)
        c1h = _candles(coin, "1h", 24 * 3600 * 1000)
        c4h = _candles(coin, "4h", 3 * 24 * 3600 * 1000)
        note = (
            f" HYPERLIQUID SNAPSHOT (public data): mark {mark:.6g}, 24h change {chg:+.1%},"
            f" funding {funding:+.4%} per hour (positive = longs pay shorts), premium {prem:+.3%},"
            f" open interest ${oi:,.0f}, 24h volume ${vol:,.0f}."
            f" Order book 0.5%: bids ${d5b:,.0f} vs asks ${d5a:,.0f} (ratio {d5b / max(d5a, 1):.2f});"
            f" 1%: bids ${d1b:,.0f} vs asks ${d1a:,.0f} (ratio {d1b / max(d1a, 1):.2f})."
            f" Largest bid wall ${bw[0] * bw[1]:,.0f} at {bw[2]:.2%} below mid, {bkeep:.0%} still present after 2.5s;"
            f" largest ask wall ${aw[0] * aw[1]:,.0f} at {aw[2]:.2%} above mid, {akeep:.0%} still present after 2.5s."
            " Walls that vanish quickly can be spoofing."
            + _candle_block(c5, "HL 5m")
            + _candle_block(c15, "HL 15m")
            + _candle_block(c1h, "HL 1h")
            + _candle_block(c4h, "HL 4h")
            + " Use HL candles + funding + OI together: prefer BUY only when short-term HL trend is up,"
            " funding not extremely positive, and bid side of book is healthier."
        )
        return note
    except Exception as ex:
        print("hl_note lỗi:", ex)
        return ""


if __name__ == "__main__":
    for t in ("BTC-USD", "ETH-USD", "SOL-USD", "TIA-USD", "INJ-USD"):
        print(t, "->", hl_note(t) or "(không có trên Hyperliquid hoặc lỗi)")
        print("-" * 60)
