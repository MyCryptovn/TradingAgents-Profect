# Paper Review Report

- Thời điểm: **2026-10-27 20:52 UTC**
- Ledger: `paper-ledger/ledger.json`
- Decisions file: `tradingagents-paper-results/decisions.json` (3 records)

## 1. Tổng quan hiệu suất

| Chỉ số | Giá trị |
|--------|---------|
| Số lệnh đã đóng | **26** |
| Lệnh đang mở | **1** |
| Win rate | **26.9%** |
| PnL trung bình / lệnh | **-1.000%** |
| Tổng PnL | **-26.00%** |
| Profit Factor | **0.58** |
| Max DD (xấp xỉ) | **42.29%** |
| Benchmark BTC (tham chiếu) | **+1.53%** |

## 2. Phân loại lý do đóng lệnh

| Lý do | Số lệnh |
|-------|---------|
| SL | 15 |
| TP | 6 |
| TIME | 2 |
| REVERSAL | 2 |
| COUNCIL | 1 |

## 3. Theo hướng BUY / SELL

| Hướng | Số lệnh | Avg PnL | Tổng PnL |
|-------|---------|---------|----------|
| BUY | 21 | -1.262% | -26.51% |
| SELL | 5 | +0.102% | +0.51% |

## 4. Top 3 vấn đề

1. Win rate thấp (26.9%).
2. Profit Factor 0.58 < 1 → kỳ vọng âm.
3. Max DD xấp xỉ 42.3% khá cao.

## 5. Top 3 điểm mạnh

1. Kỷ luật HOLD cao → tránh được nhiều lệnh nghi ngờ.
2. Có 6 lệnh chạm TP → khi đúng hướng vẫn lấy được lợi nhuận.
3. Đã có lệnh đóng theo reversal → watch_reversal đang phát huy.

## 6. 10 lệnh đóng gần nhất

| Symbol | Hướng | PnL % | Lý do |
|--------|-------|-------|-------|
| ZEC/USD | BUY | +5.59 | REVERSAL_PROFIT |
| NEAR/USD | SELL | -3.69 | SL |
| ENA/USD | BUY | -2.75 | COUNCIL |
| XCN/USD | BUY | -4.12 | SL |
| XDG/USD | SELL | -1.87 | TIME |
| USELESS/USD | BUY | -3.85 | SL |
| PEPE/USD | BUY | -3.64 | SL |
| WLD/USD | BUY | -1.39 | REVERSAL_CUT |
| HBAR/USD | BUY | -3.69 | SL |
| PYTH/USD | BUY | -3.76 | SL |

## 7. Lệnh đang mở

| Symbol | Hướng | Entry | 
|--------|-------|-------|
| AKE/USD | SELL | 0.02257864 |

## 8. Gợi ý hành động (không tự sửa code)

- Giữ rule hiện tại thêm đến khi có ≥ 20–25 lệnh đóng từ bản cấu hình mới.
- Ưu tiên xem tỷ lệ SL vs REVERSAL trên lệnh mới.
- Chỉ nới RSI/taker khi HOLD quá cao kéo dài và thiếu lệnh để đo edge.
- So sánh Total PnL với benchmark BTC trước khi cân nhắc live.

---
*Báo cáo tự động từ `scripts/paper_review.py` — chỉ dùng số liệu trong ledger.*
