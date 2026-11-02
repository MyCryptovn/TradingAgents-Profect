"""Ước tính phí và trượt giá từ sổ lệnh L2 của Hyperliquid. Chỉ đọc, không đặt lệnh."""

# Phí taker cơ bản của Hyperliquid perp (tier 0). Kiểm tra lại trên trang phí trước khi dùng.
TAKER_FEE = 0.00045   # 0.045% mỗi chiều
MAKER_FEE = 0.00015   # 0.015% mỗi chiều (chưa dùng, để tham khảo)


def _best_prices(levels):
    bids, asks = levels[0], levels[1]
    best_bid = float(bids[0]["px"]) if bids else 0.0
    best_ask = float(asks[0]["px"]) if asks else 0.0
    return best_bid, best_ask


def estimate_slippage_bps(levels, side, notional_usd):
    """
    Đi qua sổ lệnh để khớp notional_usd.
    side = "BUY" ăn phía asks, "SELL" ăn phía bids.
    Trả về trượt giá (bps) so với mid, hoặc None nếu sổ lệnh không đủ độ sâu.
    """
    best_bid, best_ask = _best_prices(levels)
    if best_bid <= 0 or best_ask <= 0:
        return None
    mid = (best_bid + best_ask) / 2

    book_side = levels[1] if side == "BUY" else levels[0]
    remaining = float(notional_usd)
    spent = 0.0
    qty = 0.0
    for row in book_side:
        px = float(row["px"])
        sz = float(row["sz"])
        if px <= 0 or sz <= 0:
            continue
        take = min(remaining, px * sz)
        spent += take
        qty += take / px
        remaining -= take
        if remaining <= 1e-9:
            break

    if remaining > 1e-9 or qty <= 0:
        return None  # không đủ độ sâu cho kích thước này

    avg_px = spent / qty
    return abs(avg_px - mid) / mid * 10000


def round_trip_cost_pct(levels_entry, levels_exit, side, notional_usd):
    """
    Chi phí khứ hồi (phần trăm) = phí vào + phí ra + trượt giá vào + trượt giá ra.
    side là hướng lệnh: BUY (long) hoặc SELL (short).
    Vào lệnh BUY mua theo asks; thoát lệnh BUY bán theo bids (đảo ngược).
    """
    exit_side = "SELL" if side == "BUY" else "BUY"
    s_in = estimate_slippage_bps(levels_entry, side, notional_usd)
    s_out = estimate_slippage_bps(levels_exit, exit_side, notional_usd)
    if s_in is None or s_out is None:
        return None
    fees = 2 * TAKER_FEE
    slip = (s_in + s_out) / 10000
    return round((fees + slip) * 100, 4)
