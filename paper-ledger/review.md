# Paper Review Report

- Thời điểm: **2026-11-03 11:02 UTC**
- Ledger: `paper-ledger/ledger.json`
- Decisions file: `tradingagents-paper-results/decisions.json` (3 records)

## 1. Tổng quan hiệu suất

| Chỉ số | Giá trị |
|--------|---------|
| Số lệnh đã đóng | **38** |
| Lệnh đang mở | **2** |
| Win rate | **31.6%** |
| PnL trung bình / lệnh | **-0.534%** |
| Tổng PnL | **-20.30%** |
| Profit Factor | **0.74** |
| Max DD (xấp xỉ) | **43.91%** |
| Benchmark BTC (tham chiếu) | **+1.53%** |

## 2. Phân loại lý do đóng lệnh

| Lý do | Số lệnh |
|-------|---------|
| SL | 18 |
| TIME | 6 |
| TP | 6 |
| REVERSAL | 6 |
| COUNCIL | 2 |

## 3. Theo hướng BUY / SELL

| Hướng | Số lệnh | Avg PnL | Tổng PnL |
|-------|---------|---------|----------|
| BUY | 25 | -1.037% | -25.92% |
| SELL | 13 | +0.432% | +5.62% |

## 4. Top 3 vấn đề

1. Win rate thấp (31.6%).
2. Profit Factor 0.74 < 1 → kỳ vọng âm.
3. Max DD xấp xỉ 43.9% khá cao.

## 5. Top 3 điểm mạnh

1. Kỷ luật HOLD cao → tránh được nhiều lệnh nghi ngờ.
2. Có 6 lệnh chạm TP → khi đúng hướng vẫn lấy được lợi nhuận.
3. Đã có lệnh đóng theo reversal → watch_reversal đang phát huy.

## 6. 10 lệnh đóng gần nhất

| Symbol | Hướng | PnL % | Lý do |
|--------|-------|-------|-------|
| ZEC/USD | SELL | -2.12 | COUNCIL |
| HYPE/USD | SELL | -0.69 | REVERSAL_CUT |
| XPL/USD | SELL | +4.01 | TIME |
| ETH/USD | BUY | +0.29 | TIME |
| ZEC/USD | SELL | -1.32 | TIME |
| NEAR/USD | SELL | +5.85 | TIME |
| BTC/USD | SELL | -0.78 | REVERSAL_CUT |
| TIA/USD | BUY | -3.69 | SL |
| HYPE/USD | BUY | +7.67 | REVERSAL_PROFIT |
| PEPE/USD | BUY | -3.68 | SL |

## 7. Lệnh đang mở

| Symbol | Hướng | Entry | 
|--------|-------|-------|
| BTC/USD | SELL | 76938.5 |
| ETH/USD | BUY | 2269.6499999999996 |

## 8. Gợi ý hành động (không tự sửa code)

- Giữ rule hiện tại thêm đến khi có ≥ 20–25 lệnh đóng từ bản cấu hình mới.
- Ưu tiên xem tỷ lệ SL vs REVERSAL trên lệnh mới.
- Chỉ nới RSI/taker khi HOLD quá cao kéo dài và thiếu lệnh để đo edge.
- So sánh Total PnL với benchmark BTC trước khi cân nhắc live.

---
*Báo cáo tự động từ `scripts/paper_review.py` — chỉ dùng số liệu trong ledger.*
