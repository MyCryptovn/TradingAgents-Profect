# Paper Review Report

- Thời điểm: **2026-10-20 20:10 UTC**
- Ledger: `paper-ledger/ledger.json`
- Decisions file: `tradingagents-paper-results/decisions.json` (2 records)

## 1. Tổng quan hiệu suất

| Chỉ số | Giá trị |
|--------|---------|
| Số lệnh đã đóng | **18** |
| Lệnh đang mở | **0** |
| Win rate | **33.3%** |
| PnL trung bình / lệnh | **-0.571%** |
| Tổng PnL | **-10.29%** |
| Profit Factor | **0.75** |
| Max DD (xấp xỉ) | **24.67%** |
| Benchmark BTC (tham chiếu) | **+1.53%** |

## 2. Phân loại lý do đóng lệnh

| Lý do | Số lệnh |
|-------|---------|
| SL | 11 |
| TP | 6 |
| TIME | 1 |

## 3. Theo hướng BUY / SELL

| Hướng | Số lệnh | Avg PnL | Tổng PnL |
|-------|---------|---------|----------|
| BUY | 15 | -1.090% | -16.35% |
| SELL | 3 | +2.022% | +6.07% |

## 4. Top 3 vấn đề

1. Sample size nhỏ (18 lệnh) → chưa đủ ý nghĩa thống kê.
2. Win rate thấp (33.3%).
3. Profit Factor 0.75 < 1 → kỳ vọng âm.

## 5. Top 3 điểm mạnh

1. Kỷ luật HOLD cao → tránh được nhiều lệnh nghi ngờ.
2. Có 6 lệnh chạm TP → khi đúng hướng vẫn lấy được lợi nhuận.
3. Hard SL kiểm soát được lỗ từng lệnh (không thấy blow-up).

## 6. 10 lệnh đóng gần nhất

| Symbol | Hướng | PnL % | Lý do |
|--------|-------|-------|-------|
| HBAR/USD | BUY | -3.69 | SL |
| PYTH/USD | BUY | -3.76 | SL |
| INJ/USD | BUY | -3.67 | SL |
| RSR/USD | BUY | -3.84 | SL |
| TIA/USD | BUY | -3.65 | SL |
| ZRO/USD | BUY | -3.74 | SL |
| W/USD | BUY | +5.21 | TP |
| BAT/USD | BUY | -3.75 | SL |
| S/USD | BUY | -3.78 | SL |
| S/USD | SELL | +5.07 | TP |

## 8. Gợi ý hành động (không tự sửa code)

- Giữ rule hiện tại thêm đến khi có ≥ 20–25 lệnh đóng từ bản cấu hình mới.
- Ưu tiên xem tỷ lệ SL vs REVERSAL trên lệnh mới.
- Chỉ nới RSI/taker khi HOLD quá cao kéo dài và thiếu lệnh để đo edge.
- So sánh Total PnL với benchmark BTC trước khi cân nhắc live.

---
*Báo cáo tự động từ `scripts/paper_review.py` — chỉ dùng số liệu trong ledger.*
