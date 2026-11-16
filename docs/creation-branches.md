# Ba nhánh sáng tạo

Mở `/create` để chọn nhánh rõ ràng:

- **Review sản phẩm:** chọn bản ghi trong Kho sản phẩm, chọn thông tin sản phẩm hoặc trải nghiệm thật do chủ sở hữu cung cấp. Link affiliate có thể gắn sau. Dữ liệu thiếu không trở thành tính năng/đánh giá đã xác minh.
- **Video viral:** nhập chủ đề để lên câu chuyện và storyboard trước; không cần chọn sản phẩm. Trend nhạy cảm vẫn bị chặn ở luồng Trend.
- **Series nhân vật:** chọn mẫu đã lưu và ý tưởng tập. Gia đình Suri có bố, mẹ và bé gái Suri 4 tuổi; chưa có ảnh tham khảo cho tới khi chủ sở hữu tải lên.

## Mỗi nhánh một prompt storyboard riêng

Prompt nằm trong `services/agent_core/prompts/`. `common.py` chỉ giữ phần chung, gồm:
- hợp đồng trường JSON;
- cách viết lời trước rồi chia nhịp, mỗi nhịp tối đa khoảng 12 từ;
- ngôn ngữ hình: mỗi nhịp có `mission` (nhiệm vụ hình ảnh), mỗi cảnh một hành động và một chuyển động máy, tránh dấu hiệu video AI rẻ tiền.

Luật của nhánh nào nằm trong file của nhánh đó; test `tests/test_branch_prompts.py` chặn luật lọt sang nhánh khác.

| | Review (`review.py`) | Viral (`viral.py`) | Series (`series.py`) |
|---|---|---|---|
| Mục tiêu | Giới thiệu thật một sản phẩm | Câu chuyện gốc xem được độc lập | Một tập theo đúng template |
| Trước khi viết | `product_truth`: sản phẩm là gì, cho ai, 2–4 tính năng có trong dữ liệu, mỗi tính năng một nhịp bằng chứng | Công thức China trend (nếu có): dùng cơ chế, không chép nội dung | Phong cách, bối cảnh, giọng kể, tính cách lấy từ template; lịch sử các tập trước |
| Nhịp | Hook ≤ 1,5 s → nỗi phiền → "thế là mình thử" → 2–4 cảnh bằng chứng → kết quả → CTA có lời | Hook → bối cảnh → diễn biến → payoff trả lời hook | Hội thoại hỏi–đáp–phản ứng, cao trào nhỏ, câu chốt |
| Giọng | Một người review xưng "mình" | Một người dẫn; nhiều giọng chỉ khi định dạng "Kể chuyện" | Hội thoại: mỗi nhịp một câu của một người, ≤ 12 từ, không giới hạn cảnh nói. Thuyết minh: một giọng kể |
| Cảnh nói vào camera | Tối đa 2 | Tối đa 2 | Hội thoại: không giới hạn |
| CTA | Có lời ("link ở giỏ hàng") và sticker "Link ở giỏ hàng" | Để rỗng | Để rỗng |
| Kiểm tra riêng | Thiếu `product_truth` hoặc nhịp bằng chứng; thiếu CTA; nhiều người nói | CTA bán hàng | Có người dẫn trong hội thoại; nhịp có lời mà không nói; câu > 12 từ |

Mọi video có clip AI đều có nhãn nhỏ "Nội dung AI" ở góc. QA báo nhịp cắt theo nhánh: Review và Viral tối đa 3,5 s mỗi cảnh, Series hội thoại 4,5 s.

## Mẫu series

Mẫu có phiên bản, tối đa 6 nhân vật. Lưu JSON tạo phiên bản mới; ảnh tham khảo PNG/JPEG/WebP tối đa 10 MB cần xác nhận quyền sử dụng. Thêm ảnh cho từng nhân vật trước khi tạo tập; thiếu ảnh chặn tạo clip Muse. Job chụp phiên bản và tài sản tham khảo khi tạo; chỉnh mẫu hoặc thêm ảnh sau đó không đổi job cũ. Tạo tập mới để dùng ảnh mới.

Nút **Tạo storyboard & video** đưa tác vụ vào hàng đợi xử lý theo cấu hình hiện tại và có thể sử dụng hạn mức AI. Giới hạn cảnh AI nằm trong Cài đặt; trang không chỉ tạo bản nháp storyboard.

Trang job hiển thị storyboard từ trạng thái kế hoạch và gợi ý affiliate lưu trong brief. Chọn **Gắn sản phẩm** để gọi bước gắn affiliate của workflow; thao tác này không tự đăng video. Các bước duyệt/đăng hiện có tiếp tục áp dụng.

Ảnh URL trong dữ liệu sản phẩm là ảnh tham khảo; chỉ ảnh đã được ghi nhận quyền sử dụng mới truyền sang phần dựng video. Bộ ngữ cảnh ghi nguồn/cache, thời điểm nhà cung cấp, cảnh báo và thông tin còn thiếu. Không có thao tác crawl bổ sung khi tạo ngữ cảnh.

Hợp đồng data.addlivetag.com hiện chỉ xác nhận ảnh chính `imageUrl`; chưa xác nhận thư viện ảnh chi tiết, mockup, thông số hoặc biến thể. Thêm ảnh chi tiết/clip trải nghiệm thật tại **Clip & hình** trong job. App chuyển ảnh và thumbnail clip có sẵn sang AI với ID và thứ tự tham khảo rõ ràng; ảnh minh họa AI không được xem là bằng chứng đã dùng sản phẩm.

Storyboard lưu đủ từng cảnh và được kiểm tra toàn bộ hạn mức trước khi đưa vào Muse. Thiếu cảnh, mất ảnh tham khảo hoặc clip đã bị xóa sẽ chuyển sang **Cần kiểm tra video**; không âm thầm bỏ cảnh hoặc dùng clip từ storyboard cũ. Gắn affiliate chỉ thực hiện khi job đã dừng để kiểm tra hoặc đã dựng xong, không viết lại video. Gợi ý affiliate hiện tìm trong danh mục có link của chủ sở hữu; không dùng link affiliate của tài khoản nhà cung cấp dữ liệu.

API nội bộ: `POST /api/create`, `POST /api/creative-templates`, `POST /api/creative-templates/{id}/references`, `POST /jobs/{id}/affiliate`. Yêu cầu ghi cần header `X-Requested-With: affiliate-agent`; không gọi mô hình LIVE chỉ bằng việc mở trang.
