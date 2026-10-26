# Paper Review Report

- Thời điểm: **2026-10-26 05:01 UTC**
- Ledger: `paper-ledger/ledger.json`
- Decisions file: `tradingagents-paper-results/decisions.json` (4 records)

## 1. Tổng quan hiệu suất

| Chỉ số | Giá trị |
|--------|---------|
| Số lệnh đã đóng | **21** |
| Lệnh đang mở | **1** |
| Win rate | **28.6%** |
| PnL trung bình / lệnh | **-0.913%** |
| Tổng PnL | **-19.16%** |
| Profit Factor | **0.62** |
| Max DD (xấp xỉ) | **33.55%** |
| Benchmark BTC (tham chiếu) | **+1.53%** |

## 2. Phân loại lý do đóng lệnh

| Lý do | Số lệnh |
|-------|---------|
| SL | 13 |
| TP | 6 |
| TIME | 1 |
| REVERSAL | 1 |

## 3. Theo hướng BUY / SELL

| Hướng | Số lệnh | Avg PnL | Tổng PnL |
|-------|---------|---------|----------|
| BUY | 18 | -1.402% | -25.23% |
| SELL | 3 | +2.022% | +6.07% |

## 4. Top 3 vấn đề

1. Win rate thấp (28.6%).
2. Profit Factor 0.62 < 1 → kỳ vọng âm.
3. Max DD xấp xỉ 33.6% khá cao.

## 5. Top 3 điểm mạnh

1. Kỷ luật HOLD cao → tránh được nhiều lệnh nghi ngờ.
2. Có 6 lệnh chạm TP → khi đúng hướng vẫn lấy được lợi nhuận.
3. Đã có lệnh đóng theo reversal → watch_reversal đang phát huy.

## 6. 10 lệnh đóng gần nhất

| Symbol | Hướng | PnL % | Lý do |
|--------|-------|-------|-------|
| USELESS/USD | BUY | -3.85 | SL |
| PEPE/USD | BUY | -3.64 | SL |
| WLD/USD | BUY | -1.39 | REVERSAL_CUT |
| HBAR/USD | BUY | -3.69 | SL |
| PYTH/USD | BUY | -3.76 | SL |
| INJ/USD | BUY | -3.67 | SL |
| RSR/USD | BUY | -3.84 | SL |
| TIA/USD | BUY | -3.65 | SL |
| ZRO/USD | BUY | -3.74 | SL |
| W/USD | BUY | +5.21 | TP |

## 7. Lệnh đang mở

| Symbol | Hướng | Entry | 
|--------|-------|-------|
| XDG/USD | SELL | 0.0762651 |

## 8. Gợi ý hành động (không tự sửa code)

- Giữ rule hiện tại thêm đến khi có ≥ 20–25 lệnh đóng từ bản cấu hình mới.
- Ưu tiên xem tỷ lệ SL vs REVERSAL trên lệnh mới.
- Chỉ nới RSI/taker khi HOLD quá cao kéo dài và thiếu lệnh để đo edge.
- So sánh Total PnL với benchmark BTC trước khi cân nhắc live.

---
*Báo cáo tự động từ `scripts/paper_review.py` — chỉ dùng số liệu trong ledger.*
