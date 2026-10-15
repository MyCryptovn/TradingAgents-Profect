"""Ảnh chụp dữ liệu công khai của Hyperliquid cho lời nhắc của hội đồng."""
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
    return ([(float(x["px"]), float(x["sz"])) for x in bids],
            [(float(x["px"]), float(x["sz"])) for x in asks])


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


def hl_note(ticker):
    coin = ticker.split("-")[0].upper()
    coin = RENAME.get(coin, coin)
    try:
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
        d5b, d5a = _depth(b1, mid, 0.005, True), _depth(a1, mid, 0.005, False)
        d1b, d1a = _depth(b1, mid, 0.01, True), _depth(a1, mid, 0.01, False)
        bw, aw = _wall(b1, mid), _wall(a1, mid)
        time.sleep(3)
        b2, a2 = _book(coin)
        bkeep = _left(b2, bw[0]) / bw[1] if bw[1] else 0
        akeep = _left(a2, aw[0]) / aw[1] if aw[1] else 0
        return (
            f" HYPERLIQUID SNAPSHOT (public data): mark {mark:.6g}, 24h change {chg:+.1%},"
            f" funding {funding:+.4%} per hour (positive means longs pay shorts), premium {prem:+.3%},"
            f" open interest ${oi:,.0f}, 24h volume ${vol:,.0f}."
            f" Order book within 0.5% of mid: bids ${d5b:,.0f} vs asks ${d5a:,.0f} (ratio {d5b / max(d5a, 1):.2f});"
            f" within 1%: bids ${d1b:,.0f} vs asks ${d1a:,.0f} (ratio {d1b / max(d1a, 1):.2f})."
            f" Largest bid wall ${bw[0] * bw[1]:,.0f} at {bw[2]:.2%} below mid, {bkeep:.0%} still present after 3 seconds;"
            f" largest ask wall ${aw[0] * aw[1]:,.0f} at {aw[2]:.2%} above mid, {akeep:.0%} still present after 3 seconds."
            " Walls that vanish within seconds can be spoofing, so do not trust the book side that vanishes."
        )
    except Exception as ex:
        print("hl_note lỗi:", ex)
        return ""


if __name__ == "__main__":
    for t in ("STRK-USD", "TIA-USD", "PYTH-USD", "BAT-USD"):
        print(t, "->", hl_note(t) or "(không có trên Hyperliquid hoặc lỗi)")
