"""Kiểm tra nhanh dữ liệu Hyperliquid và Coinalyze trước khi tin vào hội đồng. Chỉ đọc."""
import json, os, time, urllib.request, urllib.parse

HL = "https://api.hyperliquid.xyz/info"

def hl(body):
    req = urllib.request.Request(HL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def check_hl(coin="BTC"):
    now_ms = int(time.time() * 1000)
    c = hl({"type": "candleSnapshot", "req": {"coin": coin, "interval": "15m",
            "startTime": now_ms - 3 * 86400000, "endTime": now_ms}})
    closed = [r for r in c if int(r["T"]) <= now_ms]
    print(f"[HL] {coin} nến 15m đã đóng: {len(closed)}")
    if closed:
        last = closed[-1]
        age_min = (now_ms - int(last["T"])) / 60000
        print(f"[HL] nến cuối close={last['c']} tuổi={age_min:.1f} phút")
    book = hl({"type": "l2Book", "coin": coin})
    bid = float(book["levels"][0][0]["px"]); ask = float(book["levels"][1][0]["px"])
    print(f"[HL] sổ lệnh bid={bid} ask={ask} spread_bps={(ask-bid)/((ask+bid)/2)*1e4:.2f}")
    meta = hl({"type": "metaAndAssetCtxs"})
    names = [u["name"] for u in meta[0]["universe"]]
    if coin in names:
        ctx = meta[1][names.index(coin)]
        print(f"[HL] funding={ctx['funding']} openInterest={ctx['openInterest']} markPx={ctx['markPx']}")
    else:
        print(f"[HL] không tìm thấy {coin} trong universe")

def check_coinalyze(coin="BTC"):
    key = os.getenv("COINALYZE_API_KEY", "").strip()
    if not key:
        print("[CZ] thiếu COINALYZE_API_KEY (đặt biến môi trường, không ghi vào code)")
        return
    sym = f"{coin}USDT_PERP.A"
    url = "https://api.coinalyze.net/v1/open-interest?" + urllib.parse.urlencode(
        {"symbols": sym, "convert_to_usd": "true"})
    req = urllib.request.Request(url, headers={"api_key": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"[CZ] {sym} open-interest: {json.load(r)}")

if __name__ == "__main__":
    for coin in ("BTC", "ICP", "LIT"):
        check_hl(coin)
    check_coinalyze("BTC")
