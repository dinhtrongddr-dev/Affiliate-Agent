# Kế hoạch tối ưu thời gian sản xuất (14/11/2026)

**Mục tiêu:** một video 20–40 s ra bản chờ duyệt trong **12–18 phút** (hiện 35–75 phút). Nhiều video chạy chồng nhau, không xếp hàng nối tiếp.
**Bước này chỉ lập kế hoạch, chưa sửa code.**

---

## 1. Số đo hiện tại

Số liệu lấy từ các job thật ngày 13–14/11 và một lần đo riêng khâu dựng, chạy trên bản sao tạm.

| Khâu | Thời gian thật | Nguyên nhân chính |
|---|---|---|
| Xếp hàng ở worker | 0–22 phút | Worker xử lý **từng việc một**: job thứ 3 trong lượt e2e đợi 22 phút mới được viết kịch bản; khâu dựng của job này chặn khâu AI của job khác |
| AI viết kịch bản | 5,7 phút (viral) – 16,5 phút (review) | 3–6 lần gọi nối tiếp: ý tưởng, storyboard (~7.000 token), chấm, viết lại cả storyboard, chấm lại, viết lại vì lời giống báo cáo, xác nhận sản phẩm gợi ý. Bật/tắt chế độ suy nghĩ kéo dài gần như không đổi tốc độ (đo: 19 s và 25 s) |
| Muse tạo clip | 18–35 phút | Clip **nối chuỗi** (cảnh sau chờ khung cuối của cảnh trước). Khi AI xếp mọi cảnh vào cùng một địa điểm thì 8 clip chạy lần lượt. Mỗi clip 2–9 phút. Gateway cho phép 8 việc song song nhưng gần như không được dùng tới |
| Dựng video và QA | 10–20 phút | Xem bảng dưới |

**Khâu dựng một video 21,6 s, tổng 707 s:**

| Phần | Giây | Ghi chú |
|---|---|---|
| Mã hoá 2 bản hook phụ | 324 | Mã hoá lại **cả video** chỉ để đổi chữ trong 2 giây đầu |
| Mã hoá bản chính (ghép cảnh, tiếng, phụ đề) | 254 | libx264 `fast` bằng CPU i5-4590 |
| QA xem hình bằng AI | 88 | Đang chạy nối tiếp sau các bước khác |
| Dựng 6 cảnh | 67 | crf 16, lớp nền mờ được làm cả khi clip đã đúng khung 9:16 |
| Giọng VieNeu (6 câu) | 40 | Có thể làm trong lúc chờ Muse |
| QA kỹ thuật | 20 | Nhiều lượt ffmpeg đọc lại cùng một file |
| Chọn đoạn cắt | 14 | — |
| Nhạc | 7 | Có thể làm trong lúc chờ Muse |

**Bộ mã hoá có sẵn trên máy** (đo 10 s video 1080×1920):

| Bộ mã hoá | Thời gian | Khung/giây |
|---|---|---|
| libx264 `fast` (đang dùng) | 26,3 s | 11 |
| libx264 `veryfast` | 13,0 s | 23 |
| **NVENC p4** (GTX 750 Ti) | **6,1 s** | **49** |
| NVENC p1 | 3,6 s | 82 |

Quick Sync của Intel HD 4600 không khởi tạo được.

---

## 2. Vì sao app khác làm được video 45 phút nhanh hơn

Các app đó thường ghép video có sẵn (stock), ảnh và giọng đọc, nên chỉ tốn thời gian mã hoá. App này **tạo từng cảnh bằng AI video (Muse)**: mỗi clip 10 s mất 2–9 phút ở phía Muse. Đây là giới hạn dưới không vượt được, chỉ có thể **chạy song song** và **dùng ít clip hơn**. Vì vậy kế hoạch có thêm "chế độ nhanh" (mục 7) cho lúc cần số lượng.

---

## 3. T1: dựng video nhanh, từ 12 phút xuống khoảng 2–3 phút

Làm trước vì chạy trên máy, chắc chắn có lợi và ít rủi ro.

| # | Việc | Ước tính tiết kiệm |
|---|---|---|
| T1.1 | **Mã hoá bằng NVENC** (`h264_nvenc -preset p4 -cq 21`) cho bản chính, các bản hook và cảnh trung gian. Tự kiểm tra lúc khởi động; không có NVENC thì dùng libx264 `veryfast`. Thêm cấu hình `AA_VIDEO_ENCODER=auto\|nvenc\|x264` | Bản chính 254 s → khoảng 50–60 s |
| T1.2 | **Bản hook phụ chỉ mã hoá 3 giây đầu:** bản chính đặt keyframe ở giây 3. Mỗi bản phụ = mã hoá lại 3 giây đầu với chữ hook khác, rồi ghép với phần còn lại của bản chính bằng copy, không mã hoá lại | 324 s → khoảng 10 s |
| T1.3 | **Cảnh trung gian nhẹ hơn:** dùng NVENC hoặc x264 `ultrafast` (đằng nào cũng mã hoá lại ở bản chính); bỏ lớp nền mờ khi clip đã đúng 9:16 | 67 s → khoảng 20 s |
| T1.4 | **Làm trước trong lúc chờ Muse:** giọng VieNeu cho mọi câu thoại và việc chọn nhạc chỉ phụ thuộc kịch bản, nên chạy ngay khi job bắt đầu chờ Muse; lưu đệm theo (câu, giọng) | 47 s → 0 s trên đường găng |
| T1.5 | **QA song song:** QA xem hình (88 s) chạy cùng lúc với QA kỹ thuật và bước sao chép file Facebook; gộp các lượt đọc ffmpeg của QA kỹ thuật thành một | Khoảng 100 s → khoảng 90 s, chạy cùng lúc với phần khác |

**Xong khi:** đo lại bằng chính script đo hôm nay, video 20–30 s dựng xong trong **≤ 3 phút**, chất lượng hình không kém bản cũ (so ảnh khung hình).

---

## 4. T2: Muse chạy song song, từ 18–35 phút xuống khoảng 6–10 phút

| # | Việc | Ghi chú |
|---|---|---|
| T2.1 | **Bỏ nối chuỗi mặc định:** mọi clip gửi cùng lúc. Độ đồng nhất đã có ảnh nhân vật lo (QA chấm khuôn mặt 9–10/10). Chỉ nối khi AI đánh dấu 2 cảnh `continuous` liền nhau, mỗi chuỗi tối đa 2 cảnh | Thời gian = clip chậm nhất, không còn là tổng mọi clip |
| T2.2 | **Ảnh nhân vật tạo song song**, không nối chuỗi; mỗi clip chỉ chờ ảnh của người có trong cảnh đó | Nhanh hơn khoảng 1–3 phút |
| T2.3 | **Một clip, hai lần cắt:** clip 10 s cho tối đa 2 đoạn (ví dụ hành động rồi phản ứng). Video 30 s cần khoảng 5 clip thay vì 8–10 | Giảm tải Muse 40–50% |
| T2.4 | Gửi ngay khi lập kế hoạch, không đợi vòng 20 s; hỏi trạng thái 10 s/lần khi đang có việc. Giới hạn chờ một clip từ 30 xuống 12 phút để thử lại sớm | Bớt khoảng 1–2 phút |
| T2.5 | **Thử mức song song an toàn:** đo 4 rồi 8 clip một lúc trên tài khoản Muse của bạn; nếu Muse báo giới hạn hoặc lỗi nhiều thì đặt `AA_VIDEOGEN_PARALLEL` ở mức ổn định | Rủi ro: Muse có thể giới hạn số việc song song mỗi tài khoản |

---

## 5. T3: AI viết kịch bản, từ 6–17 phút xuống khoảng 3–5 phút

| # | Việc | Ghi chú |
|---|---|---|
| T3.1 | **Gộp bước ý tưởng vào storyboard** cho viral và series: còn một lần gọi | Bớt 20–60 s |
| T3.2 | **Bỏ bước "chấm, viết lại cả storyboard, chấm lại".** Storyboard tự ghi `self_review` (điểm và vấn đề). Code kiểm các luật cứng: thời lượng, số từ mỗi giây, số cảnh nói trước camera, từ báo cáo, câu rào đón, nguồn hình. Chỉ khi vi phạm mới gọi một lần **sửa đúng các câu hoặc cảnh lỗi** (JSON ngắn) | Bớt 2–10 phút; lần gọi 7.000 token thay bằng khoảng 500 token |
| T3.3 | **Storyboard gọn hơn:** bỏ các trường không dùng (`gen_prompt`, `stock_query` khi không có Pexels…), `max_tokens` từ 7.000 xuống khoảng 4.000 | AI viết ít chữ hơn thì trả lời nhanh hơn |
| T3.4 | **Xác nhận sản phẩm gợi ý chạy nền** sau khi có storyboard, song song với Muse; không chặn bước gửi clip | Bớt 30–60 s |
| T3.5 | Chế độ suy nghĩ: **giữ nguyên** (đo không thấy khác biệt) | — |

---

## 6. T4: quy trình và độ ổn định

| # | Việc | Ghi chú |
|---|---|---|
| T4.1 | **Chia worker theo làn:** làn AI (viết kịch bản), làn dựng, làn nền (trend, đồng bộ Shopee). Job B viết kịch bản trong lúc job A đang dựng. Muse vốn đã chạy riêng | 3 video không còn xếp hàng 22 phút |
| T4.2 | **Sửa lỗi e2e vừa thấy:** AI chọn `stock` cho 7/9 cảnh dù không có Pexels, nên video review dừng. Khi không có Pexels, code đổi `stock` thành `generate` hoặc ảnh sản phẩm ngay ở bước chuẩn hoá | Tránh làm lại cả job, mất 15–20 phút |
| T4.3 | **Ghi thời gian từng khâu vào job** (chờ, AI, Muse, dựng, QA) và hiện ước tính còn lại trên trang video; bộ đo chuẩn có thêm mục thời gian | Theo dõi được việc tối ưu có hiệu quả không |

---

## 7. Chế độ nhanh (tuỳ chọn trên trang Sáng tạo)

Dành cho lúc cần ra nhiều video:
- tối đa 3 clip Muse, còn lại dùng ảnh hoặc card sản phẩm, clip của bạn, hoặc cắt thêm từ chính 3 clip đó;
- 1 hook thay vì 3;
- QA xem hình chạy sau khi đã báo xong video.

Ước tính **6–8 phút** mỗi video. Chế độ đầy đủ vẫn là mặc định.

---

## 8. Kết quả dự kiến và thứ tự làm

| | Hiện tại | Sau T1 | Sau T1 + T2 | Sau T1–T4 |
|---|---|---|---|---|
| 1 video 20–40 s | 35–75 phút | 25–60 phút | 15–25 phút | **12–18 phút** |
| 3 video tạo cùng lúc | khoảng 2–3 giờ (nối tiếp) | khoảng 1,5–2 giờ | khoảng 40–60 phút | **khoảng 20–30 phút** |

**Thứ tự:**
1. T1, vì chắc chắn có lợi và chỉ chạy trên máy.
2. T4.2 (lỗi chặn video).
3. T2, kèm thử mức song song của Muse.
4. T3.
5. T4.1 và T4.3.

Mỗi bước có test, đo lại bằng script đo khâu dựng và bằng job thật, rồi commit riêng.

**Rủi ro:**
- Muse có thể giới hạn số clip song song cho mỗi tài khoản; T2.5 đo trước khi đặt mặc định.
- Driver NVENC có thể lỗi trên vài máy; đã có đường lùi về x264 `veryfast`.
- Bỏ nối chuỗi có thể làm mối nối giữa các cảnh kém mượt hơn; QA xem hình sẽ so sánh với bản cũ.

---

## 9. Kết quả e2e 3 nhánh (đang chạy)

| Nhánh | Trạng thái lúc lập kế hoạch | Ghi chú |
|---|---|---|
| Review (Cocoon) | **Dừng** ở "Cảnh 2 chưa có stock hợp lệ" | Lỗi T4.2. AI viết kịch bản mất 16,5 phút |
| Viral (bố tết tóc) | Đang chờ Muse | Clip chạy lần lượt (T2.1) |
| Series Suri | Đang chờ Muse | Lần đầu chạy series với Muse thật: ảnh nhân vật đã tạo, 1 ảnh lỗi đang tạo lại |
