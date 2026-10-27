# Paper Review Report

- Thời điểm: **2026-10-27 10:01 UTC**
- Ledger: `paper-ledger/ledger.json`
- Decisions file: `tradingagents-paper-results/decisions.json` (4 records)

## 1. Tổng quan hiệu suất

| Chỉ số | Giá trị |
|--------|---------|
| Số lệnh đã đóng | **24** |
| Lệnh đang mở | **2** |
| Win rate | **25.0%** |
| PnL trung bình / lệnh | **-1.163%** |
| Tổng PnL | **-27.90%** |
| Profit Factor | **0.53** |
| Max DD (xấp xỉ) | **42.29%** |
| Benchmark BTC (tham chiếu) | **+1.53%** |

## 2. Phân loại lý do đóng lệnh

| Lý do | Số lệnh |
|-------|---------|
| SL | 14 |
| TP | 6 |
| TIME | 2 |
| REVERSAL | 1 |
| COUNCIL | 1 |

## 3. Theo hướng BUY / SELL

| Hướng | Số lệnh | Avg PnL | Tổng PnL |
|-------|---------|---------|----------|
| BUY | 20 | -1.605% | -32.10% |
| SELL | 4 | +1.049% | +4.20% |

## 4. Top 3 vấn đề

1. Win rate thấp (25.0%).
2. Profit Factor 0.53 < 1 → kỳ vọng âm.
3. Max DD xấp xỉ 42.3% khá cao.

## 5. Top 3 điểm mạnh

1. Kỷ luật HOLD cao → tránh được nhiều lệnh nghi ngờ.
2. Có 6 lệnh chạm TP → khi đúng hướng vẫn lấy được lợi nhuận.
3. Đã có lệnh đóng theo reversal → watch_reversal đang phát huy.

## 6. 10 lệnh đóng gần nhất

| Symbol | Hướng | PnL % | Lý do |
|--------|-------|-------|-------|
| ENA/USD | BUY | -2.75 | COUNCIL |
| XCN/USD | BUY | -4.12 | SL |
| XDG/USD | SELL | -1.87 | TIME |
| USELESS/USD | BUY | -3.85 | SL |
| PEPE/USD | BUY | -3.64 | SL |
| WLD/USD | BUY | -1.39 | REVERSAL_CUT |
| HBAR/USD | BUY | -3.69 | SL |
| PYTH/USD | BUY | -3.76 | SL |
| INJ/USD | BUY | -3.67 | SL |
| RSR/USD | BUY | -3.84 | SL |

## 7. Lệnh đang mở

| Symbol | Hướng | Entry | 
|--------|-------|-------|
| ZEC/USD | BUY | 1219.79 |
| NEAR/USD | SELL | 4.4252 |

## 8. Gợi ý hành động (không tự sửa code)

- Giữ rule hiện tại thêm đến khi có ≥ 20–25 lệnh đóng từ bản cấu hình mới.
- Ưu tiên xem tỷ lệ SL vs REVERSAL trên lệnh mới.
- Chỉ nới RSI/taker khi HOLD quá cao kéo dài và thiếu lệnh để đo edge.
- So sánh Total PnL với benchmark BTC trước khi cân nhắc live.

---
*Báo cáo tự động từ `scripts/paper_review.py` — chỉ dùng số liệu trong ledger.*
