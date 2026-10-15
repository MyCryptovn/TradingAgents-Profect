#!/usr/bin/env python3
"""Hyperliquid DRY-RUN: chỉ ĐỌC dữ liệu công khai và IN kế hoạch lệnh. Không có khóa, không gửi lệnh."""
import json
import math
import os
import urllib.request

from scripts.paper_ledger import price

LEDGER_PATH = os.getenv("LEDGER_FILE", "paper-ledger/ledger.json")
NOTIONAL = float(os.getenv("NOTIONAL_USD", "20"))
MAX_DEV = float(os.getenv("MAX_PRICE_DEVIATION", "0.05"))
SL = float(os.getenv("STOP_LOSS_PCT", "3")) / 100
TP = float(os.getenv("TAKE_PROFIT_PCT", "6")) / 100
RENAME = {"XBT": "BTC", "XDG": "DOGE"}
URL = "https://api.hyperliquid.xyz/info"


def info(body):
    req = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fmt_px(p, sz_dec):
    p = float(f"{p:.5g}")
    return round(p, max(0, 6 - sz_dec))


def plan(t, mids, meta):
    coin = t["symbol"].split("/")[0]
    coin = RENAME.get(coin, coin)
    if coin not in mids or coin not in meta:
        return f"BỎ QUA {t['symbol']}: không có trên Hyperliquid"
    hl = float(mids[coin])
    kr = price(t["pair"])
    dev = abs(hl / kr - 1)
    if dev > MAX_DEV:
        return (f"BỎ QUA {t['symbol']}: giá lệch {dev:.1%} "
                f"(Hyperliquid {hl:g} so với Kraken {kr:g}), có thể khác tài sản")
    dec = meta[coin]["szDecimals"]
    size = math.floor(NOTIONAL / hl * 10 ** dec) / 10 ** dec
    if size <= 0 or size * hl < 10:
        return f"BỎ QUA {t['symbol']}: khối lượng quá nhỏ, dưới mức tối thiểu của sàn"
    d = 1 if t["action"] == "BUY" else -1
    sl = fmt_px(hl * (1 - d * SL), dec)
    tp = fmt_px(hl * (1 + d * TP), dec)
    return (f"[DRY-RUN] {t['action']} {coin} khối lượng {size:g} (~${size * hl:,.2f}), đòn bẩy 1x, "
            f"giá hiện tại {hl:g}, cắt lỗ {sl:g}, chốt lời {tp:g} "
            f"(hai lệnh trigger reduce-only sẽ đặt trên sàn)")


def main():
    with open(LEDGER_PATH) as f:
        trades = json.load(f)["trades"]
    real = [t for t in trades if t["action"] in ("BUY", "SELL") and not t.get("base")]
    open_t = [t for t in real if t["status"] == "open"]
    src = open_t or real[-5:]
    label = "đang mở" if open_t else "mẫu (5 lệnh gần nhất, vì không có lệnh nào đang mở)"
    print(f"Sổ: {LEDGER_PATH} | lệnh {label}: {len(src)} | vốn mỗi lệnh: ${NOTIONAL:g}")
    if not src:
        print("Sổ chưa có lệnh BUY/SELL nào để mô phỏng.")
        return
    mids = info({"type": "allMids"})
    meta = {u["name"]: u for u in info({"type": "meta"})["universe"]}
    for t in src:
        try:
            print(plan(t, mids, meta))
        except Exception as ex:
            print("Lỗi", t["symbol"], ex)
    print("Đây là chế độ chạy khô: KHÔNG có lệnh nào được gửi.")


if __name__ == "__main__":
    main()
