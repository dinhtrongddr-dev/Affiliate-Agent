# Kế hoạch: China Trend Intelligence & Content Director (14/11/2026)

**Đầu vào:** bản kế hoạch "China Trend Intelligence Engine – AI Viral Content Automation" do chủ dự án gửi, đối chiếu với code thật và các job đã chạy hôm nay.
**Bước này chỉ lập kế hoạch, chưa sửa code.**
**Vòng khép kín cần đạt:** DISCOVER → ANALYZE → PREDICT → CREATE → PRODUCE → VALIDATE → PUBLISH → LEARN → OPTIMIZE.

---

## 1. Hiện trạng: đã có gì, thiếu gì

| Hạng mục trong kế hoạch gốc | Hiện trạng trong app | Đánh giá |
|---|---|---|
| Thu thập trend | `trend_intel/collectors.py` lấy Google Trends VN và Google News VN (518 mốc quan sát); YouTube và Trends-MCP cần key; TikTok chỉ có dán tay. **Chưa có nguồn Trung Quốc nào** | Có khung, thiếu nguồn |
| Phân tích tăng trưởng | `analyzer.py`: growth, acceleration, z-score, vòng đời; `scoring.py`: 7 thành phần, độ tin cậy, an toàn; AI phân tích ý định mua (gom lô, lưu 7 ngày) | Dùng lại được; cần chuẩn hoá thêm cho nguồn Trung Quốc |
| Chấm điểm cơ hội | Có, nhưng thiên về **mua hàng** (`affiliate × trend`). Chưa có điểm "Việt hoá được", "sản xuất được bằng app", độ mới lạ hay độ trùng với video đã làm | Thiếu nhánh chấm cho mục tiêu Facebook |
| Phát hiện ngách | Chưa có phân loại ngách | Thiếu |
| Chuyển thể Trung → Việt | Chưa có. `viral_creator` sáng tác từ một chủ đề, chưa tách "công thức viral" ra từ trend gốc | Thiếu; đây là phần cốt lõi |
| Content Director | Có 3 nhánh, storyboard v2, phong cách quay điện thoại hoặc sitcom, dàn giọng, hook A/B. Chưa có lớp "định dạng" (giải thích, phim tài liệu ngắn, quy trình, 2.5D/3D). Thời lượng cố định theo nhánh (15–40 s) | Có nền, thiếu định dạng và thời lượng linh hoạt |
| Kiểm soát chất lượng | QA xem hình và đo thật, có các cổng chặn, có chấm điểm. Chưa tự sửa có giới hạn, chưa kiểm tra tính xác thực, chưa kiểm tra tính nguyên bản | Có nền, thiếu tự sửa và nguyên bản |
| Facebook Learning | Có đặc điểm video, nhập số liệu tay, điểm tương tác, bài học vào prompt khi đủ 10 video, trang `/performance`. Chưa có số liệu thật nào; chưa có ngách, định dạng, nguồn trend trong đặc điểm | Có khung, chờ dữ liệu |
| Dashboard | `/trends` (trend VN), `/jobs`, `/tasks`, `/performance` | Cần thêm tab Trung Quốc, Ngách, Ý tưởng |
| Hiệu năng | T1 xong: dựng 707 s → 166 s. **Muse vẫn chạy nối tiếp**; worker một luồng; AI gọi 3–6 lần mỗi video | Phải làm xong trước khi tăng số lượng |

**Kết quả e2e hôm nay:**
- **Viral "bố tết tóc":** đạt (QA 85,9, dài 25,2 s).
- **Review Cocoon:** dừng ở "Cảnh 2 chưa có stock hợp lệ", vì AI chọn stock dù không có Pexels.
- **Series Suri:** đang dựng; app vẫn chạy code dựng cũ vì chưa khởi động lại sau T1.

**Kết luận:** khoảng 60% hạ tầng kế hoạch gốc cần đã có sẵn: khung trend, sáng tạo, QA, học. Phần thiếu thật sự gồm: nguồn Trung Quốc, chuyển thể công thức viral, ngách và định dạng, tự sửa có giới hạn và kiểm tra nguyên bản, hàng đợi ý tưởng có mục tiêu Facebook/affiliate tách riêng. Một điều kiện tiên quyết là **tốc độ và độ ổn định của pipeline**. Đẩy nhiều ý tưởng hơn vào một pipeline 35–75 phút mỗi video, lại còn lỗi chặn, thì không ra thêm video.

---

## 2. Nguồn trend Trung Quốc: khả năng thực tế

Đây là kết quả tìm hiểu tài liệu cộng đồng. **Chưa thử thật**: mọi nguồn phải qua bước kiểm thử GĐ1.1 trước khi đưa vào app.

| Nền tảng | Cách lấy dự kiến | Dữ liệu | Mức khả thi |
|---|---|---|---|
| **Bilibili** | `api.bilibili.com/x/web-interface/popular` và `ranking/v2`: JSON công khai, không đăng nhập (theo tài liệu cộng đồng) | Tiêu đề, mô tả, tag, thời lượng, ảnh bìa, lượt xem, thích, chia sẻ, bình luận, tác giả | **Cao**, dữ liệu giàu nhất cho bước phân tích công thức |
| **Weibo** | Danh sách hot search công khai, như cách newsnow (MIT) và DailyHotApi đang lấy | Chủ đề, độ nóng, thứ hạng | Trung bình–cao; chỉ có chủ đề, không có video |
| **Douyin** | Bảng hot search/billboard mà các dự án tổng hợp đang dùng | Từ khoá, độ nóng | **Chưa chắc**: có thể cần cookie hoặc chữ ký; nếu cần vượt cơ chế bảo vệ thì đánh dấu "chưa hỗ trợ" |
| **Kuaishou** | Bảng hot nếu có endpoint công khai | Chủ đề, độ nóng | Chưa chắc |
| **Xiaohongshu** | Không thấy danh sách hot công khai; có chữ ký chống thu thập | — | **Không tự động**: chỉ nhập tay URL, ghi chú, ảnh chụp màn hình |
| **WeChat Channels** | Không có API công khai | — | Chỉ nhập tay |
| Zhihu, Baidu, Toutiao (bổ sung) | Danh sách hot công khai qua cùng cách | Chủ đề kiến thức và xã hội | Tuỳ chọn, cho ngách A và E |

**Nguyên tắc áp dụng:**
- Chỉ lấy metadata và số liệu công khai, không tải video gốc.
- Không vượt CAPTCHA, chữ ký hay giới hạn truy cập.
- Mỗi nguồn có ngân sách gọi, thời gian chờ và cơ chế tự ngắt khi lỗi liên tục.
- Nguồn hỏng không làm hỏng app; nguồn không hỗ trợ được ghi rõ là "chưa hỗ trợ", không tạo dữ liệu giả.
- Chỉ **viết lại bằng Python** các bộ lấy dữ liệu cần dùng, tham khảo newsnow (MIT, ghi công trong mã), **không cài cả dự án Node**.

**Lưu ý:** điều khoản của các nền tảng có thể không cho truy cập tự động, kể cả với trang công khai. Bạn cần quyết định chấp nhận mức này hay chỉ dùng nhập tay (mục 10).

---

## 3. GĐ0: ổn định và tăng tốc pipeline (làm trước, 2–3 ngày)

Phần này là kế hoạch tối ưu thời gian đang dở (`2026-11-14-speed-plan.md`), cộng thêm các lỗi tìm được qua e2e.

| # | Việc | Xong khi |
|---|---|---|
| 0.1 | Sửa lỗi stock khi không có Pexels: code đổi cảnh stock sang `generate` hoặc ảnh sản phẩm ngay ở bước chuẩn hoá (T4.2) | Review Cocoon chạy hết, không dừng |
| 0.2 | Muse song song (T2): bỏ nối chuỗi mặc định, ảnh nhân vật tạo song song, một clip cắt 2 đoạn, đo mức song song an toàn của tài khoản | Khâu Muse ≤ 10 phút cho video 30 s |
| 0.3 | AI gọn (T3): gộp bước ý tưởng vào storyboard; bỏ vòng "chấm rồi viết lại cả storyboard"; chỉ sửa đúng câu lỗi | Khâu AI ≤ 5 phút |
| 0.4 | Worker chia làn (T4.1): làn AI, làn dựng, làn nền (trend, đồng bộ) | 3 job chạy chồng nhau, không xếp hàng |
| 0.5 | Ghi thời gian từng khâu vào job và hiện trên trang video (T4.3) | Thấy được video đang chờ ở khâu nào |
| 0.6 | Chạy lại e2e 3 nhánh trên code mới | Cả 3 đạt; mỗi video ≤ 18 phút |

---

## 4. GĐ1: thu thập trend Trung Quốc (MVP, 2–3 ngày)

| # | Việc | Chi tiết |
|---|---|---|
| 1.1 | **Thử từng nguồn** | Script đo từng endpoint (Bilibili, Weibo, Douyin, Kuaishou, Zhihu): có trả dữ liệu không, có cần đăng nhập không, giới hạn tần suất. Kết quả là **bảng hỗ trợ** ghi trong tài liệu; nguồn không đạt thì đánh dấu "chưa hỗ trợ" |
| 1.2 | **Bộ lấy dữ liệu** `trend_intel/collectors_cn.py` | Bắt đầu với 2 nguồn ổn định nhất (dự kiến Bilibili và Weibo). Mỗi lượt lấy là một mốc quan sát `record_signal` với nguồn `bilibili`, `weibo`…, kèm thứ hạng, độ nóng, lượt xem, chia sẻ. Lấy 2–3 giờ/lần ở làn nền |
| 1.3 | **Dịch và gắn nhãn bằng AI** | Gom lô 20 chủ đề mỗi lần gọi ChatGPT2API, trả về: tên tiếng Việt, tóm tắt, nhóm ngách, có hợp với Việt Nam không, cảnh báo nhạy cảm. Lưu 7 ngày; trend đã có không dịch lại |
| 1.4 | **Lưu trữ** | Thêm `region` ('vn'/'cn'), `title_original`, `platform_url` vào `trend_topics`; ảnh bìa Bilibili lưu tạm và dọn sau 7 ngày. Không lưu video |
| 1.5 | **Nhập tay** | Dán URL hoặc ghi chú Douyin, Xiaohongshu, Kuaishou, WeChat; tuỳ chọn tải lên ảnh chụp màn hình hoặc clip tham khảo **do bạn tự lưu**, chỉ dùng để phân tích, không bao giờ đưa vào video (mục 10) |
| 1.6 | **Giao diện** | Trang `/trends` có tab "Trung Quốc": tên Việt, tên gốc, nền tảng, link, thời điểm phát hiện, ngách, giai đoạn, điểm, độ tin cậy, trạng thái |

**Xong khi:** ít nhất 2 nguồn chạy tự động 48 giờ không lỗi; có ≥ 50 trend được dịch và gắn nhãn; nguồn không hỗ trợ được ghi rõ.

---

## 5. GĐ2: phân tích, ngách, chấm điểm (2 ngày)

| # | Việc | Chi tiết |
|---|---|---|
| 2.1 | **4 giai đoạn** | Early Signal / Rising / Peak / Saturated, dựa trên chuỗi thứ hạng và độ nóng theo thời gian (tận dụng growth và acceleration sẵn có). Dữ liệu dưới 3 mốc thì ghi **"độ tin cậy thấp"**, không đoán |
| 2.2 | **Chuẩn hoá giữa các nền tảng** | Quy đổi theo bách phân vị trong từng nguồn (độ nóng Weibo và lượt xem Bilibili không so trực tiếp được); đếm số nền tảng cùng có trend để tính "lan truyền" |
| 2.3 | **Điểm cơ hội nội dung** (mục tiêu Facebook, tách khỏi điểm affiliate hiện có) | Tăng trưởng 25, lan truyền 20, tương tác/chia sẻ 20, Việt hoá được 15 (AI), sản xuất được bằng app 10 (AI: cần người thật, con vật hay 3D? Muse làm được không?), thương mại 10 (dùng lại bộ ghép sản phẩm đã đạt 100% trên bộ có nhãn). Cộng thêm: độ tin cậy, độ mới lạ, **độ trùng với video đã làm** (so với hook và chủ đề trong `video_features`), cảnh báo bản quyền và chính sách Facebook (mở rộng `safety.py`) |
| 2.4 | **Ngách** | 6 nhóm ban đầu (A Kiến thức trực quan, B Kỹ năng và nghề lạ, C Mini drama, D Mẹo đời sống, E Người thật và cảm xúc, F Trend ngắn hạn). AI gán ngách khi dịch; khi ≥ 5 trend mới không vừa nhóm nào và cùng một cụm thì **đề xuất ngách mới** để bạn duyệt |
| 2.5 | **Mục tiêu** | Mỗi cơ hội có nhãn `goal`: `fb_growth` hoặc `affiliate`, mỗi loại có KPI riêng (mục 8) |

**Xong khi:** bảng xếp hạng cơ hội có nguồn, thời điểm, giai đoạn, độ tin cậy và lý do AI chọn; kiểm tra tay 20 trend đầu thấy hợp lý.

---

## 6. GĐ3: chuyển thể Trung → Việt và Content Director (3–4 ngày)

Đây là phần quan trọng nhất.

| # | Việc | Chi tiết |
|---|---|---|
| 3.1 | **Tách công thức viral** | Từ metadata (tiêu đề, mô tả, tag, thời lượng, số liệu), ảnh bìa (AI xem ảnh) và clip tham khảo nếu bạn cung cấp (dùng lại khâu trích khung hình đã có), AI trả JSON: hook 1–3 s hoạt động thế nào, điều giữ người xem, cao trào hoặc twist, lý do chia sẻ, **công thức tái dùng được**, điều cần đổi cho hợp văn hoá Việt Nam |
| 3.2 | **≥ 3 ý tưởng gốc mỗi trend** | Mỗi ý tưởng có: premise, hook dự kiến, định dạng, thời lượng, ngách, mục tiêu, điểm chất lượng dự kiến. Lưu vào bảng `ideas` (trang "Ý tưởng"); bạn chọn, hoặc chế độ tự động lấy ý tưởng điểm cao |
| 3.3 | **Lớp định dạng** | `explainer`, `story`, `mini_doc`, `process` (quy trình trước/sau), `product`, `animated` (2.5D/3D). Mỗi định dạng có phong cách quay riêng (thêm `animated_3d` và `documentary` vào `style_profiles`), nhịp, và cách dùng giọng (người dẫn hay nhân vật) |
| 3.4 | **Thời lượng theo định dạng** | 10–25 s (thị giác, kỹ năng), 25–45 s (khám phá, viral), 30–60 s (kiến thức, mẹo, review), 60–120 s (chuyện nhiều tình tiết). Từ 60 s trở lên tốn 10–20 clip Muse, khoảng 30–60 phút ngay cả khi chạy song song: chỉ dùng khi định dạng cần (mục 10) |
| 3.5 | **Kiểm tra nguyên bản** | So kịch bản của mình với tiêu đề, mô tả trend gốc (độ giống chữ và độ giống ý do AI chấm); cấm dịch lại nguyên văn; ghi nguồn cảm hứng trong hồ sơ video (nội bộ) |
| 3.6 | **Kiểm tra xác thực** cho ngách kiến thức | Storyboard liệt kê các khẳng định thực tế kèm độ chắc chắn; khẳng định chắc chắn thấp thì bị viết lại cho mềm hoặc bỏ; video kiến thức luôn **cần duyệt tay** trước khi đăng |
| 3.7 | **Nối vào pipeline** | Ý tưởng được chọn thành job `viral_creator` hoặc `template_series` (hay `product_review` khi mục tiêu affiliate), brief mang theo công thức, định dạng, thời lượng. Dùng lại storyboard v2, Muse, giọng, QA, hook A/B |

**Xong khi:** từ 1 trend Bilibili hoặc Weibo tạo được ≥ 3 ý tưởng; 1 ý tưởng đi hết pipeline thành video đạt QA; kiểm tra nguyên bản không phát hiện chép.

---

## 7. GĐ4: cổng chất lượng và sản xuất tự động (2–3 ngày)

| # | Việc | Chi tiết |
|---|---|---|
| 4.1 | **3 mức kết luận** | **Đạt** (sang chờ đăng); **Cần sửa** (tự sửa đúng phần lỗi); **Không đạt** (loại hoặc tạo lại). Dựa trên `qa_score`, các cổng chặn sẵn có, và thêm nguyên bản, xác thực, giọng (VieNeu đọc lỗi hay không, đo qua độ khớp lời và thời lượng) |
| 4.2 | **Tự sửa có giới hạn** | Phụ đề hoặc chữ lỗi: dựng lại (gần như miễn phí). Câu thoại dở: sửa đúng câu, mỗi video 1 lần. Clip lỗi hình (tay méo, đổi mặt): tạo lại đúng clip đó, tối đa 1 lần. Quá giới hạn thì "Cần duyệt tay". Có trần số lượt AI và Muse cho mỗi video |
| 4.3 | **Hàng đợi sản xuất** | Chế độ tự động (tắt mặc định): mỗi ngày lấy N ý tưởng điểm cao theo tỉ lệ mục tiêu (ví dụ 70% `fb_growth`, 30% `affiliate`), có trần video và clip Muse mỗi ngày. Tab "Hàng đợi" gom theo trạng thái: chờ, đang làm, cần sửa, đã xong, đã đăng, lỗi |
| 4.4 | **Đăng** | Giữ nguyên: mặc định duyệt tay; chỉ tự đăng khi bật `AA_AUTO_PUBLISH` **và** bật riêng cho từng mục tiêu; video kiến thức và review luôn duyệt tay; video AI chân thực luôn bật nhãn nội dung AI khi đăng |

---

## 8. GĐ5: học và tự tối ưu (1–2 ngày, rồi chờ dữ liệu)

| # | Việc | Chi tiết |
|---|---|---|
| 5.1 | **Thêm vào đặc điểm video** | Ngách, định dạng, nguồn trend, nền tảng gốc, giai đoạn trend lúc làm, **điểm cơ hội dự đoán** |
| 5.2 | **KPI tách theo mục tiêu** | `fb_growth`: % giữ 3 s, % xem hết, chia sẻ, follower mới, lượt xem đủ điều kiện kiếm tiền. `affiliate`: click, đơn, hoa hồng. Nhập tay như hiện nay; tự lấy Insights chỉ khi bạn cấp quyền (mục 10). Số liệu nào chưa nhập thì hiện "thiếu", không giả lập |
| 5.3 | **So dự đoán với kết quả** | Theo ngách, định dạng, nguồn: điểm cơ hội dự đoán so với điểm tương tác thật |
| 5.4 | **Điều chỉnh có kiểm soát** | Đề xuất tăng hoặc giảm tỉ trọng ngách và trọng số chấm điểm chỉ khi mỗi nhóm có ≥ 5 video có số liệu; mỗi lần đổi tối đa ±20%; một video viral bất thường (vượt 3 lần trung vị) không được làm đổi chiến lược. Áp dụng thay đổi phải qua bạn duyệt |
| 5.5 | **Giao diện** | `/performance` thêm biểu đồ theo ngách, định dạng, nguồn và mục "đề xuất tuần này" |

---

## 9. Kiến trúc và hiệu năng (phù hợp máy i5-4590, 12 GB RAM, GTX 750 Ti)

- **Giữ một tiến trình app**, chia worker theo làn (GĐ0.4). Bộ thu thập Trung Quốc chạy ở làn nền, có ngân sách gọi, thời gian chờ và ngắt khi lỗi theo từng nguồn. Chưa tách thành dịch vụ riêng: chi phí vận hành tăng mà máy không cần; sẽ xem lại khi số nguồn hoặc số job vượt khả năng.
- **Không chạy model lớn trên máy.** LLM qua ChatGPT2API, video qua Muse; trên máy chỉ có ffmpeg (NVENC), VieNeu và các phép tính nhẹ.
- **Lưu đệm:** bản dịch và nhãn trend (7 ngày), kết quả xác nhận cặp trend–sản phẩm (đã có), giọng đọc (đã có), ảnh bìa (dọn sau 7 ngày). Trend không đổi thứ hạng thì không gọi AI lại.
- **Cách ly lỗi:** mọi lỗi của Trend Engine chỉ ghi log và hiện trạng thái nguồn, không chặn tạo video hay đăng bài.
- **Test:** mỗi bộ lấy dữ liệu có test với dữ liệu mẫu đã lưu sẵn (không gọi mạng thật trong test); kiểm tra thật được báo riêng.

---

## 10. Quyết định cần bạn

1. **Thứ tự:** làm GĐ0 (tốc độ, ổn định, lỗi e2e) trước rồi mới tới nguồn Trung Quốc? Mình khuyến nghị **có**.
2. **Mức chấp nhận nguồn:** dùng các danh sách hot công khai không cần đăng nhập (Bilibili, Weibo, Douyin nếu thử được), biết rằng điều khoản nền tảng có thể không cho truy cập tự động; hay chỉ nhập tay?
3. **Clip tham khảo:** cho phép bạn tải lên clip hoặc ảnh chụp trend gốc mà **bạn tự lưu**, chỉ để AI phân tích công thức, không bao giờ dùng trong video?
4. **Định dạng dài và 3D:** có làm video 60–120 s và phong cách 2.5D/3D không, biết rằng mỗi video tốn 10–20 clip Muse, khoảng 30–60 phút?
5. **Sản xuất tự động:** trần video mỗi ngày bao nhiêu, và tỉ lệ `fb_growth` : `affiliate`? Đăng vẫn mặc định duyệt tay.
6. **Số liệu Facebook:** tiếp tục nhập tay, hay cho phép lấy Insights tự động nếu bạn cấp token có quyền đọc số liệu?

---

## 11. Ước lượng và thứ tự

| Giai đoạn | Ước lượng | Phụ thuộc |
|---|---|---|
| GĐ0 Ổn định và tốc độ | 2–3 ngày | — |
| GĐ1 Thu thập Trung Quốc | 2–3 ngày | GĐ0.4 (làn nền); quyết định 2 |
| GĐ2 Phân tích, ngách, chấm điểm | 2 ngày | GĐ1 |
| GĐ3 Chuyển thể và Content Director | 3–4 ngày | GĐ2; quyết định 3, 4 |
| GĐ4 Cổng chất lượng và sản xuất tự động | 2–3 ngày | GĐ3; quyết định 5 |
| GĐ5 Học và tối ưu | 1–2 ngày, rồi chờ dữ liệu | Có video đã đăng và số liệu; quyết định 6 |

Mỗi giai đoạn kết thúc bằng: test, chạy thật một vòng, báo cáo phần đã xong, phần chưa xong và rủi ro, rồi commit.

**Tiêu chuẩn MVP** (theo mục XI của kế hoạch gốc) đạt khi xong GĐ0–GĐ4 và chạy được một vòng:

> trend thật từ nguồn đã xác minh → phân tích, xếp hạng → ≥ 3 ý tưởng → video hoàn chỉnh → qua QA → chờ duyệt hoặc đăng → lưu lịch sử

mà không có lỗi hồi quy (toàn bộ test hiện có vẫn qua). GĐ5 chỉ coi là xong khi đã có số liệu đăng thật.

---

**Tham khảo:**
- [newsnow (MIT)](https://hellogithub.com/en/repository/ourongxing/newsnow) và [README](https://cdn.jsdelivr.net/gh/ourongxing/newsnow@main/README.md)
- [DailyHotApi](https://hub.docker.com/r/imsyy/dailyhot-api) (chưa xác nhận giấy phép)
- Bilibili popular API theo tài liệu cộng đồng: [dev.to](https://dev.to/sami_8858131362756585e4f4/the-easiest-chinese-platform-to-scrape-in-python-in-2026-bilibili-in-under-30-lines-5216), [apify](https://apify.com/crawlerbros/bilibili-scraper.md)

Tất cả đều là nguồn cộng đồng, phải thử thật ở GĐ1.1.

---

## 12. Trạng thái thực hiện (14/11/2026)

| Giai đoạn | Trạng thái | Ghi chú |
|---|---|---|
| GĐ0 | Xong | Commit `9e535fd`: Muse song song, storyboard một lần gọi AI, worker chia làn, ghi thời gian từng khâu, sửa lỗi stock. T1 ở commit `f11ccc7` |
| GĐ1 | Xong | Bilibili, Douyin, Weibo, Toutiao tự động (đã thử thật); Kuaishou, Xiaohongshu, WeChat nhập tay; Zhihu không dùng |
| GĐ2 | Xong | 4 giai đoạn, chuẩn hoá theo bách phân vị, điểm cơ hội, ngách và đề xuất ngách mới, mục tiêu Facebook/affiliate |
| GĐ3 | Xong | Công thức viral và ≥ 3 ý tưởng; 6 định dạng; kiểm chứng kiến thức; kiểm tra nguyên bản |
| GĐ4 | Xong | Cổng chất lượng Đạt/Sửa/Không đạt; chế độ tự động (mặc định tắt) |
| GĐ5 | Code xong, chờ dữ liệu | KPI theo mục tiêu, so dự đoán với thực tế, đề xuất có giới hạn. Chưa có số liệu đăng thật |

Hướng dẫn dùng: `docs/china-trends.md`.
