# Paper Review Report

- Thời điểm: **2026-11-04 01:36 UTC**
- Ledger: `paper-ledger/ledger.json`
- Decisions file: `tradingagents-paper-results/decisions.json` (7 records)

## 1. Tổng quan hiệu suất

| Chỉ số | Giá trị |
|--------|---------|
| Số lệnh đã đóng | **41** |
| Lệnh đang mở | **2** |
| Win rate | **29.3%** |
| PnL trung bình / lệnh | **-0.621%** |
| Tổng PnL | **-25.48%** |
| Profit Factor | **0.70** |
| Max DD (xấp xỉ) | **43.91%** |
| Benchmark BTC (tham chiếu) | **+1.53%** |

## 2. Phân loại lý do đóng lệnh

| Lý do | Số lệnh |
|-------|---------|
| SL | 19 |
| TIME | 6 |
| TP | 6 |
| REVERSAL | 6 |
| COUNCIL | 2 |
| UNKNOWN | 2 |

## 3. Theo hướng BUY / SELL

| Hướng | Số lệnh | Avg PnL | Tổng PnL |
|-------|---------|---------|----------|
| BUY | 26 | -1.024% | -26.62% |
| SELL | 15 | +0.076% | +1.14% |

## 4. Top 3 vấn đề

1. Win rate thấp (29.3%).
2. Profit Factor 0.70 < 1 → kỳ vọng âm.
3. Max DD xấp xỉ 43.9% khá cao.

## 5. Top 3 điểm mạnh

1. Kỷ luật HOLD cao → tránh được nhiều lệnh nghi ngờ.
2. Có 6 lệnh chạm TP → khi đúng hướng vẫn lấy được lợi nhuận.
3. Đã có lệnh đóng theo reversal → watch_reversal đang phát huy.

## 6. 10 lệnh đóng gần nhất

| Symbol | Hướng | PnL % | Lý do |
|--------|-------|-------|-------|
| XPL/USD | SELL | -3.64 | SL |
| ETH/USD | BUY | -0.70 | None |
| BTC/USD | SELL | -0.84 | None |
| ZEC/USD | SELL | -2.12 | COUNCIL |
| HYPE/USD | SELL | -0.69 | REVERSAL_CUT |
| XPL/USD | SELL | +4.01 | TIME |
| ETH/USD | BUY | +0.29 | TIME |
| ZEC/USD | SELL | -1.32 | TIME |
| NEAR/USD | SELL | +5.85 | TIME |
| BTC/USD | SELL | -0.78 | REVERSAL_CUT |

## 7. Lệnh đang mở

| Symbol | Hướng | Entry | 
|--------|-------|-------|
| XMR/USD | BUY | 634.41 |
| XRP/USD | BUY | 1.27745 |

## 8. Gợi ý hành động (không tự sửa code)

- Giữ rule hiện tại thêm đến khi có ≥ 20–25 lệnh đóng từ bản cấu hình mới.
- Ưu tiên xem tỷ lệ SL vs REVERSAL trên lệnh mới.
- Chỉ nới RSI/taker khi HOLD quá cao kéo dài và thiếu lệnh để đo edge.
- So sánh Total PnL với benchmark BTC trước khi cân nhắc live.

---
*Báo cáo tự động từ `scripts/paper_review.py` — chỉ dùng số liệu trong ledger.*
