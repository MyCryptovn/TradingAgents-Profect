# Paper Review Report

- Thời điểm: **2026-10-22 14:02 UTC**
- Ledger: `paper-ledger/ledger.json`
- Decisions file: `tradingagents-paper-results/decisions.json` (2 records)

## 1. Tổng quan hiệu suất

| Chỉ số | Giá trị |
|--------|---------|
| Số lệnh đã đóng | **19** |
| Lệnh đang mở | **0** |
| Win rate | **31.6%** |
| PnL trung bình / lệnh | **-0.614%** |
| Tổng PnL | **-11.67%** |
| Profit Factor | **0.73** |
| Max DD (xấp xỉ) | **26.06%** |
| Benchmark BTC (tham chiếu) | **+1.53%** |

## 2. Phân loại lý do đóng lệnh

| Lý do | Số lệnh |
|-------|---------|
| SL | 11 |
| TP | 6 |
| TIME | 1 |
| REVERSAL | 1 |

## 3. Theo hướng BUY / SELL

| Hướng | Số lệnh | Avg PnL | Tổng PnL |
|-------|---------|---------|----------|
| BUY | 16 | -1.109% | -17.74% |
| SELL | 3 | +2.022% | +6.07% |

## 4. Top 3 vấn đề

1. Sample size nhỏ (19 lệnh) → chưa đủ ý nghĩa thống kê.
2. Win rate thấp (31.6%).
3. Profit Factor 0.73 < 1 → kỳ vọng âm.

## 5. Top 3 điểm mạnh

1. Kỷ luật HOLD cao → tránh được nhiều lệnh nghi ngờ.
2. Có 6 lệnh chạm TP → khi đúng hướng vẫn lấy được lợi nhuận.
3. Đã có lệnh đóng theo reversal → watch_reversal đang phát huy.

## 6. 10 lệnh đóng gần nhất

| Symbol | Hướng | PnL % | Lý do |
|--------|-------|-------|-------|
| WLD/USD | BUY | -1.39 | REVERSAL_CUT |
| HBAR/USD | BUY | -3.69 | SL |
| PYTH/USD | BUY | -3.76 | SL |
| INJ/USD | BUY | -3.67 | SL |
| RSR/USD | BUY | -3.84 | SL |
| TIA/USD | BUY | -3.65 | SL |
| ZRO/USD | BUY | -3.74 | SL |
| W/USD | BUY | +5.21 | TP |
| BAT/USD | BUY | -3.75 | SL |
| S/USD | BUY | -3.78 | SL |

## 8. Gợi ý hành động (không tự sửa code)

- Giữ rule hiện tại thêm đến khi có ≥ 20–25 lệnh đóng từ bản cấu hình mới.
- Ưu tiên xem tỷ lệ SL vs REVERSAL trên lệnh mới.
- Chỉ nới RSI/taker khi HOLD quá cao kéo dài và thiếu lệnh để đo edge.
- So sánh Total PnL với benchmark BTC trước khi cân nhắc live.

---
*Báo cáo tự động từ `scripts/paper_review.py` — chỉ dùng số liệu trong ledger.*
