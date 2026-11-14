# Trend Trung Quốc → nội dung Việt

Trang **🇨🇳 Trend Trung Quốc** (`/china`) phát hiện xu hướng video ngắn ở Trung Quốc, rút ra công thức viral và sáng tác video **mới** cho người Việt bằng pipeline sẵn có: storyboard v2, Muse, giọng VieNeu, QA, hook A/B.

## Vòng chạy

1. **Thu thập** (3 giờ/lần, làn nền), từ danh sách hot công khai, không đăng nhập, không tải video:
   - Bilibili: video phổ biến, kèm lượt xem/thích/chia sẻ và ảnh bìa;
   - Douyin: hot search (từ khoá, độ nóng, số video);
   - Weibo: hot search thời gian thực;
   - Toutiao: bảng tin nóng.

   Kuaishou, Xiaohongshu, WeChat Channels chỉ nhập tay. Zhihu không dùng vì cần đăng nhập. Một nguồn lỗi 3 lần liên tiếp thì tự dừng 6 giờ, không ảnh hưởng nguồn khác.
2. **Gắn nhãn bằng AI** (20 trend mỗi lần gọi, tối đa 60 trend mỗi lượt; làm lại sau 7 ngày): tên và tóm tắt tiếng Việt, ngách, định dạng hợp lý, mức Việt hoá được, mức app sản xuất được, tiềm năng thương mại, rủi ro Facebook và bản quyền. Trend nhạy cảm bị **chặn**.
3. **Giai đoạn:** Tín hiệu sớm, Đang lên, Đỉnh, Bão hoà. Trend dưới 3 mốc dữ liệu ghi **độ tin cậy thấp**, không đoán.
4. **Điểm cơ hội (0–100):**
   - tăng trưởng 25, lan truyền giữa nền tảng 20, tương tác 20, Việt hoá 15, sản xuất được 10, thương mại 10;
   - nhân hệ số giai đoạn, rủi ro và độ trùng với video kênh đã làm;
   - thành phần nào không đo được thì bỏ ra và hạ độ tin cậy.
5. **Ý tưởng:** mỗi lượt, 3 trend điểm cao chưa có ý tưởng được tự sinh ý tưởng; bấm **💡 Ý tưởng** để sinh cho trend khác. AI rút **công thức viral** (hook, điều giữ người xem, cao trào, lý do chia sẻ, điều cần đổi cho Việt Nam, điều không được chép) và viết **≥ 3 ý tưởng gốc** có định dạng, thời lượng, ngách, mục tiêu, điểm dự kiến.
6. **Sản xuất:** bấm **🎬 Sản xuất** hoặc bật chế độ tự động. Ý tưởng thành job với định dạng, khoảng thời lượng, công thức và nguồn trend. Ý tưởng affiliate chỉ gắn sản phẩm đã qua bước AI xác nhận cặp.
7. **Cổng chất lượng:** sau khi dựng có 3 kết luận:
   - **Đạt:** chờ duyệt;
   - **Sửa:** tạo lại một lần những cảnh mà QA xem hình đánh dấu lỗi;
   - **Không đạt:** chuyển sang "Cần kiểm tra" kèm lý do.
8. **Học:** trang **Hiệu quả & bài học** có KPI theo mục tiêu, so điểm dự đoán với kết quả thật, và đề xuất điều chỉnh. Đề xuất chỉ xuất hiện khi mỗi nhóm có ≥ 5 video; video viral bất thường bị bỏ ra; mỗi lần đổi tối đa 20%. Mọi thay đổi do bạn duyệt.

## Định dạng

| Định dạng | Thời lượng | Phong cách | Kiểm chứng kiến thức |
|---|---|---|---|
| Giải thích kiến thức | 30–60 s | Phóng sự | Có |
| Kể chuyện | 20–45 s | Quay điện thoại | — |
| Phim tài liệu ngắn | 40–90 s | Phóng sự | Có |
| Quy trình trước/sau | 15–35 s | Quay điện thoại | — |
| Giới thiệu sản phẩm | 20–40 s | Quay điện thoại | — |
| Hoạt hình 2.5D/3D | 20–60 s | Hoạt hình 3D | — |

Video kiến thức phải liệt kê các khẳng định kèm độ chắc chắn; khẳng định dưới 70 bị viết lại. Trang video hiện danh sách này để bạn kiểm trước khi duyệt.

## Nhập tay

Tab **Nguồn & tự động** cho phép nhập nền tảng, chủ đề, link, ghi chú và tuỳ chọn tải lên ảnh hoặc clip **bạn tự lưu** (tối đa 80 MB). File này chỉ để AI phân tích công thức, không bao giờ được đưa vào video.

## Cài đặt (nhóm "Trend Trung Quốc")

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `AA_CN_TRENDS_ENABLED` | bật | Thu thập trend Trung Quốc |
| `AA_CN_AUTO_IDEAS` | 3 | Số trend tự sinh ý tưởng mỗi lượt |
| `AA_AUTOPILOT_ENABLED` | **tắt** | Tự sản xuất từ ý tưởng điểm cao |
| `AA_AUTOPILOT_DAILY_VIDEOS` | 3 | Số video tự sản xuất tối đa mỗi ngày |
| `AA_AUTOPILOT_MIN_SCORE` | 70 | Điểm ý tưởng tối thiểu |
| `AA_AUTOPILOT_GOAL_MIX` | `fb_growth:70,affiliate:30` | Tỉ lệ mục tiêu |
| `AA_QA_AUTOFIX` | bật | Cổng chất lượng tự tạo lại cảnh lỗi (1 lần) |

Đăng bài vẫn theo quy tắc cũ: mặc định duyệt tay; chỉ tự đăng khi bạn bật `AA_AUTO_PUBLISH`.

## Giới hạn và lưu ý

- Điều khoản của các nền tảng có thể không cho truy cập tự động, kể cả với trang công khai. App chỉ đọc danh sách hot công khai với tần suất thấp, không vượt đăng nhập, chữ ký hay CAPTCHA.
- Trend chỉ có một nền tảng và một mốc dữ liệu thì độ tin cậy thấp. Độ tin cậy tăng dần qua các lượt thu thập 3 giờ/lần.
- Cùng một chủ đề nhưng tiêu đề khác nhau giữa các nền tảng được gộp theo độ giống của tên tiếng Việt (ngưỡng 85).
- Định dạng 60–120 s cần 10–20 clip Muse.
