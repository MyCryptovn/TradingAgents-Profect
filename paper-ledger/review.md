# Paper Review Report

- Thời điểm: **2026-10-29 06:00 UTC**
- Ledger: `paper-ledger/ledger.json`
- Decisions file: `tradingagents-paper-results/decisions.json` (3 records)

## 1. Tổng quan hiệu suất

| Chỉ số | Giá trị |
|--------|---------|
| Số lệnh đã đóng | **29** |
| Lệnh đang mở | **0** |
| Win rate | **27.6%** |
| PnL trung bình / lệnh | **-1.018%** |
| Tổng PnL | **-29.52%** |
| Profit Factor | **0.58** |
| Max DD (xấp xỉ) | **43.91%** |
| Benchmark BTC (tham chiếu) | **+1.53%** |

## 2. Phân loại lý do đóng lệnh

| Lý do | Số lệnh |
|-------|---------|
| SL | 17 |
| TP | 6 |
| REVERSAL | 3 |
| TIME | 2 |
| COUNCIL | 1 |

## 3. Theo hướng BUY / SELL

| Hướng | Số lệnh | Avg PnL | Tổng PnL |
|-------|---------|---------|----------|
| BUY | 22 | -1.372% | -30.19% |
| SELL | 7 | +0.096% | +0.67% |

## 4. Top 3 vấn đề

1. Win rate thấp (27.6%).
2. Profit Factor 0.58 < 1 → kỳ vọng âm.
3. Max DD xấp xỉ 43.9% khá cao.

## 5. Top 3 điểm mạnh

1. Kỷ luật HOLD cao → tránh được nhiều lệnh nghi ngờ.
2. Có 6 lệnh chạm TP → khi đúng hướng vẫn lấy được lợi nhuận.
3. Đã có lệnh đóng theo reversal → watch_reversal đang phát huy.

## 6. 10 lệnh đóng gần nhất

| Symbol | Hướng | PnL % | Lý do |
|--------|-------|-------|-------|
| PEPE/USD | BUY | -3.68 | SL |
| INJ/USD | SELL | -3.70 | SL |
| AKE/USD | SELL | +3.86 | REVERSAL_PROFIT |
| ZEC/USD | BUY | +5.59 | REVERSAL_PROFIT |
| NEAR/USD | SELL | -3.69 | SL |
| ENA/USD | BUY | -2.75 | COUNCIL |
| XCN/USD | BUY | -4.12 | SL |
| XDG/USD | SELL | -1.87 | TIME |
| USELESS/USD | BUY | -3.85 | SL |
| PEPE/USD | BUY | -3.64 | SL |

## 8. Gợi ý hành động (không tự sửa code)

- Giữ rule hiện tại thêm đến khi có ≥ 20–25 lệnh đóng từ bản cấu hình mới.
- Ưu tiên xem tỷ lệ SL vs REVERSAL trên lệnh mới.
- Chỉ nới RSI/taker khi HOLD quá cao kéo dài và thiếu lệnh để đo edge.
- So sánh Total PnL với benchmark BTC trước khi cân nhắc live.

---
*Báo cáo tự động từ `scripts/paper_review.py` — chỉ dùng số liệu trong ledger.*
