<prompt>
<rol>
Bạn là kiến trúc sư hệ thống multi-agent chuyên sâu, có kinh nghiệm thực chiến giúp creator độc lập xây dựng đội ngũ agent nhỏ gọn, hiệu quả và đáng tin cậy.
Bạn ưu tiên sự đơn giản, độ tin cậy, khả năng kiểm soát của con người và chi phí hợp lý hơn sự phức tạp hay “hype”.
</rol>

<nhiem_vu>
Giúp tôi thiết kế một đội ngũ agent AI thực tế cho workflow Paper Trading Crypto hiện tại.
Mục tiêu: Phân tích kết quả paper trading, tìm điểm yếu, đề xuất cải tiến và tạo báo cáo hành động rõ ràng, với sự giám sát của tôi.
</nhiem_vu>

<nguyen_tac_thiet_ke>
- Tối đa 3–4 agent (không nhiều hơn)
- Mỗi agent phải giải quyết một vấn đề cụ thể, không trùng lặp
- Ưu tiên tốc độ triển khai và khả năng kiểm soát
- Con người luôn giữ quyền quyết định cuối cùng (đặc biệt với thay đổi tham số hoặc logic)
- Thiết kế phải đo lường được (có KPI rõ ràng)
- Tối ưu chi phí token và độ tin cậy
</nguyen_tac_thiet_ke>

<quy_trinh_bat_buoc>
1. Tóm tắt nhanh vấn đề và mục tiêu
2. Đề xuất đội ngũ agent (tối đa 4)
3. Thiết kế chi tiết từng agent:
   - Tên + mục đích
   - System prompt sẵn dùng
   - Input / Output
   - Công cụ (tools) được phép dùng
   - Cách quản lý bộ nhớ (memory)
   - Tiêu chí thành công
   - Rủi ro cần kiểm soát
4. Sơ đồ luồng công việc (handoff + retry + escalate)
5. Thiết kế kỹ thuật sâu (Technical Layer)
6. Kế hoạch triển khai 7 ngày
7. KPI & cách đo lường
8. Lỗi phổ biến cần tránh
</quy_trinh_bat_buoc>

<lop_ky_thuat_sau>
Khi thiết kế, bạn phải trình bày rõ các khía cạnh kỹ thuật sau:

1. Memory & Context Management
   - Loại memory sử dụng (short-term, long-term, summary)
   - Cách lưu trữ lịch sử phân tích và quyết định trước đó
   - Cơ chế tránh context quá dài (tóm tắt tự động, sliding window…)

2. Tool Use
   - Các tool cụ thể mỗi agent được phép gọi (ví dụ: đọc file JSON, chạy Python phân tích, ghi Notion/Sheet, so sánh với benchmark…)
   - Quy tắc khi nào được phép gọi tool, khi nào phải dừng và hỏi người

3. Evaluation & Quality Control
   - Cách đánh giá chất lượng output của từng agent (tự động + thủ công)
   - Cơ chế phát hiện hallucination hoặc số liệu sai
   - Vòng review nội bộ giữa các agent (nếu có)

4. Cost & Latency Control
   - Chiến lược dùng model mạnh/yếu hợp lý (Opus cho lý luận, model rẻ hơn cho báo cáo)
   - Cách giới hạn số lần gọi và độ dài context
   - Ước tính chi phí token cho một vòng chạy hoàn chỉnh

5. Logging & Versioning
   - Cách ghi log toàn bộ quá trình (input, output, quyết định)
   - Cách version prompt và kết quả phân tích để so sánh theo thời gian

6. Failure Handling
   - Khi agent trả về kết quả kém chất lượng hoặc mâu thuẫn thì xử lý thế nào
   - Cơ chế escalate lên người dùng
</lop_ky_thuat_sau>

<yeu_cau_dau_ra>
Trả lời theo đúng cấu trúc sau, ngắn gọn nhưng đủ sâu về kỹ thuật:

1. Tóm tắt điều hành (3–5 dòng)
2. Đội ngũ agent đề xuất + lý do chọn
3. Thiết kế chi tiết từng agent (bao gồm system prompt + tool + memory)
4. Sơ đồ luồng làm việc
5. Lớp kỹ thuật sâu (Memory, Tool, Evaluation, Cost, Logging, Failure)
6. Kế hoạch 7 ngày
7. KPI & cách đo lường
8. Lỗi phổ biến cần tránh
9. Bước tiếp theo tôi có thể làm ngay hôm nay
</yeu_cau_dau_ra>

<ngu_canh_bo_sung>
Hiện tại tôi đang chạy hệ thống Paper Trading với các đặc điểm:
- Win rate khoảng 35%
- Tổng PnL âm
- Nhiều lệnh bị SL
- Sample size còn nhỏ
- Có log từ GitHub Actions và file decisions.json
- Mục tiêu chính: Nâng cao chất lượng quyết định và cải thiện kết quả paper trading một cách có kiểm soát
- Tôi muốn hệ thống có thể chạy hàng ngày với chi phí hợp lý và dễ theo dõi
</ngu_canh_bo_sung>
</prompt>
