"""Công thức PnL duy nhất cho toàn bộ hệ thống paper. Không dùng ở nơi khác."""

FEE_PER_SIDE = 0.00045      # phí taker Hyperliquid tier 0, mỗi chiều (xem lại trên trang phí)
SLIP_PER_SIDE = 0.0005      # trượt giá ước lượng mỗi chiều (0.05%); thay bằng cost_model khi có sổ lệnh
ROUND_TRIP_COST = 2 * (FEE_PER_SIDE + SLIP_PER_SIDE)


def direction(action: str) -> int:
    return 1 if action == "BUY" else -1


def pnl_pct(action: str, entry: float, exit_px: float) -> float:
    """PnL sau phí, theo phần trăm. BUY = long, SELL = short."""
    if not entry or not exit_px:
        raise ValueError("entry/exit phải > 0")
    d = direction(action)
    return round((d * (exit_px / entry - 1) - ROUND_TRIP_COST) * 100, 3)
