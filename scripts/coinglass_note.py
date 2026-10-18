"""CoinGlass note (funding + OI + liquidation tổng hợp).
Cần biến môi trường COINGLASS_API_KEY.
Khi chưa có key → trả về chuỗi rỗng, không làm crash pipeline.
"""
import json
import os
import urllib.request

URL = "https://open-api-v4.coinglass.com"
API_KEY = os.getenv("COINGLASS_API_KEY", "").strip()


def _get(path, params=None):
    if not API_KEY:
        return None
    q = ""
    if params:
        q = "?" + "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(
        URL + path + q,
        headers={
            "CG-API-KEY": API_KEY,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.load(r)
    if str(data.get("code")) != "0":
        return None
    return data.get("data")


def coinglass_note(ticker: str) -> str:
    """Trả về chuỗi ngắn về funding/OI/liquidation tổng hợp. Không có key → ''."""
    if not API_KEY:
        return ""

    coin = ticker.split("-")[0].upper()
    if coin in ("XBT",):
        coin = "BTC"

    try:
        # Funding theo exchange (lấy vài sàn chính)
        funding_list = _get("/api/futures/funding-rate/exchange-list", {"symbol": coin}) or []
        # OI tổng hợp gần nhất
        oi_list = _get("/api/futures/open-interest/exchange-list", {"symbol": coin}) or []
        # Liquidation 24h gần đúng
        liq = _get("/api/futures/liquidation/coin-list") or []

        parts = []

        if funding_list:
            # Lấy funding trung bình vài sàn lớn nếu có
            rates = []
            for x in funding_list[:8]:
                try:
                    rates.append(float(x.get("rate") or x.get("fundingRate") or 0))
                except Exception:
                    pass
            if rates:
                avg = sum(rates) / len(rates)
                parts.append(f"CG funding avg \~{avg:+.4%} across exchanges")

        if oi_list:
            try:
                total_oi = sum(float(x.get("openInterest") or x.get("oi") or 0) for x in oi_list[:10])
                if total_oi > 0:
                    parts.append(f"CG aggregated OI hint ${total_oi:,.0f}")
            except Exception:
                pass

        # Liquidation nhanh (nếu có symbol trùng)
        if isinstance(liq, list):
            for row in liq:
                sym = str(row.get("symbol") or row.get("coin") or "").upper()
                if sym == coin:
                    long_liq = row.get("longLiquidation") or row.get("long_liq") or 0
                    short_liq = row.get("shortLiquidation") or row.get("short_liq") or 0
                    parts.append(f"CG recent liq long ${float(long_liq):,.0f} / short ${float(short_liq):,.0f}")
                    break

        if not parts:
            return ""

        return (
            " COINGLASS (aggregated derivatives): "
            + "; ".join(parts)
            + ". Use as confirmation with Hyperliquid: extreme funding + rising OI + heavy liquidation = higher reversal risk."
        )
    except Exception as ex:
        print("coinglass_note lỗi:", ex)
        return ""


if __name__ == "__main__":
    print(coinglass_note("BTC-USD") or "(chưa có COINGLASS_API_KEY hoặc lỗi)")
