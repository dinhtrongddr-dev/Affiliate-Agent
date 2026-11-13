# Báo cáo bước 2: chất lượng nội dung (13/11/2026)

**Phạm vi:** toàn bộ chuỗi tạo nội dung, từ trend/sản phẩm qua ChatGPT2API (ý tưởng, kịch bản, storyboard, tự chấm) và Muse (clip AI) tới FFmpeg (giọng, phụ đề, nhạc, chuyển cảnh), QA, gắn link.

**Nguồn bằng chứng:**
- Mã nguồn hiện tại, gồm cả phần chưa commit.
- 3 storyboard thật (chó chạy, "Ai là người hiểu mẹ nhất", video base ốp lưng).
- 8 prompt đã gửi sang Muse.
- Ảnh khung hình của 3 video thành phẩm.
- Nhật ký nhạc.
- 11 trend đã ghép sản phẩm từ kho thật (301 sản phẩm).

Lỗi chức năng của bước 1 nằm ở phần cuối (mục 6). Bước 2 tập trung vào **chất lượng**.

---

## 1. Kết luận ngắn

| Hạng mục | Đánh giá | Ghi chú |
|---|---|---|
| Ý tưởng & cấu trúc câu chuyện | **Khá** | AI viết có hook, nút thắt, payoff; câu chuyện "mẹ" có chiều sâu cảm xúc thật |
| Storyboard chi tiết | **Tốt** | Từng shot có hành động, bối cảnh, camera, ánh sáng, continuity |
| Đồng bộ nhân vật | **Trung bình** | Trang phục giữ được nhờ lặp mô tả; khuôn mặt vẫn trôi giữa các cảnh vì **không có ảnh tham chiếu nào được gửi** |
| Độ "chân thực, đời thường" | **Yếu** | Muse được yêu cầu "cinematic/photorealistic", giọng kể ngôi thứ ba, 3 lớp chữ cùng lúc, hook "Ủa/Khoan" → nhìn ra ngay là video AI |
| Prompt video | **Trung bình** | Đủ ý nhưng mỗi shot 8–9 nghìn ký tự, kèm chuỗi nội bộ vô nghĩa với Muse |
| Hậu kỳ (giọng, phụ đề, nhạc) | **Yếu** | Phụ đề chồng nhau; mọi video dùng **cùng một bài nhạc**; karaoke canh giờ ước lượng |
| Đánh giá chất lượng tự động | **Không đáng tin** | Điểm nền 78,5/100, cộng theo từ khoá → video nào cũng 90+ "ưu tiên đăng" |
| Gắn đúng sản phẩm | **Trung bình** | Khi khớp thì chuẩn (cờ vua ↔ Lê Quang Liêm); vẫn còn nhiều ghép sai (máy xay ↔ cáp sạc) |
| Vòng học từ hiệu suất | **Chưa có** | Module tồn tại nhưng không được gọi, không có nguồn số liệu |

**Tóm lại:** app đã có "bộ não" kịch bản và storyboard tốt, nhưng **khâu hình–tiếng–chữ cuối cùng làm video lộ chất AI và giống nhau**, còn bộ chấm điểm thì không phát hiện được điều đó. Muốn viral, nâng cấp phải tập trung vào: chân thực, đa dạng, chấm điểm thật, và vòng học.

---

## 2. Điểm mạnh (giữ nguyên)

1. **Ba nhánh sáng tạo rõ ràng:** review sản phẩm, video viral, series nhân vật. Mỗi nhánh có mục tiêu riêng; video viral không bị ép CTA bán hàng.
2. **Storyboard dạng hợp đồng:** mỗi cảnh có `shot.action / setting / camera / lighting / emotion / continuity / dialogue / sound / transition`. Video "mẹ" có 8 shot nối tiếp logic: hỏi vui → câu hỏi khó → im lặng → con út nhận ra → cả nhà làm → mẹ được ngồi ăn.
3. **Khoá trang phục hoạt động:** mọi gen_prompt lặp lại "navy-blue polo / sage-green blouse / rust cardigan / mustard-yellow T-shirt". Ảnh khung hình cho thấy trang phục giữ đúng qua 8 cảnh.
4. **Trung thực dữ liệu:** quy tắc cấm bịa giá/công dụng; `creation_context` tách sự thật sản phẩm, nguồn dữ liệu và trải nghiệm do chủ kênh cung cấp; ảnh chưa có quyền không được đưa vào dựng.
5. **Tự chấm và viết lại bằng AI:** dog video được AI chấm 7,9, liệt kê 5 vấn đề (payoff yếu, mô-típ quen, thoại trùng), rồi viết lại.
6. **Chuẩn kỹ thuật đầu ra:** 1080×1920, 30 fps, −14 LUFS, không khung đen/đứng hình, phụ đề trong vùng an toàn của Reels/Shopee.
7. **An toàn trend:** chặn tai nạn, chính trị, xổ số trước khi gọi AI; trend không bán được tự rơi xuống cuối.

---

## 3. Điểm yếu chi tiết (kèm bằng chứng)

### A. Chưa chân thực, dễ nhận ra là AI

| # | Vấn đề | Bằng chứng | Tác động |
|---|---|---|---|
| A1 | Prompt Muse ép phong cách điện ảnh | `STYLE` và storyboard dùng "cinematic", "photorealistic", "smooth cinematic camera movement"; style video mẹ: "Cinematic realistic Vietnamese family sitcom" | Hình bóng bẩy, ánh sáng studio, giống quảng cáo AI; người xem Reels lướt qua vì không giống clip điện thoại |
| A2 | Giọng kể ngôi thứ ba thay vì nhân vật nói | Mọi prompt Muse đều có "No speech"; tiếng gốc của Muse bị tắt; TTS đọc "Người con út chợt nhận ra điều đó…" | Video thành phim tài liệu có người dẫn; mất cảm giác đời thường và mất khẩu hình (Muse có thể cho nhân vật nói câu trong ngoặc kép) |
| A3 | Ba lớp chữ cùng lúc | Ảnh khung hình: hook đóng khung + nhãn chữ IN HOA phía trên + karaoke vàng phía dưới, có cả emoji 🏆 | Nhìn như template máy làm; che mặt nhân vật |
| A4 | Hook rập khuôn | Ví dụ trong `HOOK_FORMULAS` ("Ủa! Nó đang đuổi theo cái gì…") bị bắt chước gần nguyên văn ("KHOAN—NÓ ĐANG LAO VỀ PHÍA AI?"); `generate_hook_variations` dùng câu cố định "Ủa! Khoan đã, chuyện này là sao vậy mọi người?" | Mọi video mở đầu na ná nhau → thuật toán và người xem nhận ra mẫu |
| A5 | Một giọng đọc, một tốc độ | `AA_TTS_VOICE=Trúc Ly`, `+10%` cho mọi video; review sản phẩm và chuyện gia đình dùng cùng giọng | Thiếu cá tính kênh và biến hoá theo nội dung |
| A6 | Hashtag lẫn tên nội bộ và tiếng Anh | Cả 2 video viral có `#ViralCreator`; video chó có `#DogRunning #CountrysideLife` | Lộ hệ thống, kém hợp khán giả VN |

### B. Kịch bản và hook

| # | Vấn đề | Bằng chứng | Tác động |
|---|---|---|---|
| B1 | Chỉ một kịch bản cho mỗi video, không A/B | `hook_options` được sinh ra nhưng không dùng; `AA_VARIANTS` không có tác dụng | Không thử được hook nào giữ chân tốt hơn |
| B2 | Hai hệ prompt song song | `trend_plan_prompt`/`review_prompt` (cũ) và `branch_plan_prompt`/`branch_review_prompt` (mới) cùng tồn tại; luật khác nhau, thời lượng khác nhau | Chất lượng không đồng đều giữa các luồng; khó bảo trì |
| B3 | Heuristic "giả AI" | `evaluate_creative_idea`, `determine_duration_strategy` đoán cảm xúc/khán giả theo từ khoá ("mẹ" → cảm động); `generate_hook_variations` trả điểm cố định 9,0/9,2/9,1 | Trái yêu cầu "không dùng mẫu cứng"; hiện chỉ test dùng, dễ bị nối nhầm vào luồng thật |
| B4 | Thời lượng không kiểm soát | Video mẹ dài **48,8 giây** (8 shot × 6–7 s); `RULES` mô tả nhịp tới 32 s; video base thì chỉ 5,3 s | Reels/Shopee ưu tiên 15–30 s cho viral; 49 s narrated dễ rớt người xem giữa chừng |
| B5 | Chấm lại sau khi viết lại bị cắt cụt | Lần chấm lại storyboard dùng `max_tokens=600` | Điểm sau khi viết lại có thể hỏng hoặc thiếu |

### C. Storyboard và đồng bộ nhân vật

| # | Vấn đề | Bằng chứng | Tác động |
|---|---|---|---|
| C1 | **Không gửi ảnh tham chiếu** cho Muse ở nhánh viral | 8/8 task video "mẹ" có `refs_json=[]`; ảnh nhân vật (character sheet) không được tạo trước ở nhánh storyboard | Khuôn mặt mẹ và bố đổi giữa các cảnh; video "Test topic" trước đó lệch rõ (áo mẹ đổi màu, trẻ con khác người) |
| C2 | Mỗi shot là một lần gọi Muse độc lập | Không dùng ảnh khung cuối cảnh N làm ảnh đầu cảnh N+1 | Mối nối nhảy (vị trí ngồi, ánh sáng đổi) |
| C3 | Series "Gia đình Suri" chưa có ảnh | Mẫu v1 có 0 ảnh tham khảo | Nhánh series chưa dùng được, mà đây là nhánh dễ tạo kênh trung thành nhất |

### D. Prompt video cho Muse

| # | Vấn đề | Bằng chứng | Tác động |
|---|---|---|---|
| D1 | Prompt quá dài | 8.144–9.014 ký tự mỗi shot | Muse là agent dạng chat, prompt dài làm loãng ý chính; khó kiểm soát nội dung được ưu tiên |
| D2 | Rác nội bộ trong prompt | "Reference manifest digest: 4f53cd…", "Dialogue is script context only…" | Tốn ngữ cảnh, có thể sinh chữ/ký tự lạ |
| D3 | Thời lượng không khớp | Prompt ghi "approximately 6 seconds"/"Requested duration: 7 seconds", nhưng Muse luôn ra khoảng 10 s; render kéo cảnh theo giọng đọc | Phần thừa bị cắt; hành động chính có thể rơi vào đoạn bị cắt |
| D4 | Sản phẩm thật không được thấy | Ảnh từ AddLive có quyền `unknown` nên không được gửi; nhánh review chỉ mô tả sản phẩm bằng chữ | Muse **tự vẽ** sản phẩm → khác hàng thật ở link → rủi ro gây hiểu lầm cho người mua |

### E. Hậu kỳ: phụ đề, giọng, nhạc

| # | Vấn đề | Bằng chứng | Tác động |
|---|---|---|---|
| E1 | Phụ đề chồng 2 dòng | Ảnh khung hình video mẹ: "ghế cho mẹ." đè lên "chỉ lặng lẽ kéo". Lời đọc dài hơn cảnh nên lời cảnh trước đè sang cảnh sau | Khó đọc, lộ lỗi |
| E2 | Karaoke canh giờ ước lượng | VieNeu không trả mốc thời gian từng từ → `evenly_timed_words` chia đều | Chữ chạy lệch với tiếng |
| E3 | **Mọi video cùng một bài nhạc** | Nhật ký: "Mixkit Track 738" được chọn cho mood "Happy", "Upbeat" lẫn "Emotional, Warm"; luôn chấm 85/100; tên bài và tác giả không đọc được ("Mixkit Creator") | Các video nghe giống nhau; nhạc vui đặt dưới chuyện cảm động |
| E4 | Nhạc lấy từ việc tự đọc trang Mixkit | `MixkitMusicClient` đọc HTML với User-Agent giả Chrome; không lưu thông tin bài nhạc vào manifest (`music: null`) | Phụ thuộc trang web, không truy được nguồn bài nhạc; cần kiểm tra điều khoản Mixkit |
| E5 | Cảnh kéo dài bằng cách đóng băng khung cuối | `tpad=stop_mode=clone` khi lời đọc dài hơn clip | Có đoạn hình đứng yên trong khi giọng vẫn đọc |
| E6 | Bảng tình trạng báo "Nhạc nền (0 bài)" | Thực tế nhạc vẫn được thêm từ Mixkit | Báo sai cho người dùng |

### F. Đánh giá chất lượng (QA)

| # | Vấn đề | Bằng chứng | Tác động |
|---|---|---|---|
| F1 | Điểm sáng tạo là tổng các điểm nền | `evaluate_creative_quality`: điểm nền 15+12+12+12+7,5+8+8+4 = 78,5; cộng điểm nếu có chữ "mẹ", "vui", có `music_mood`… | Cả 4 video đều 93–99 "PRIORITY_PUBLISH", kể cả video 5,3 s không có sản phẩm hợp ngữ cảnh |
| F2 | QA không nhìn hình | Không kiểm tra sản phẩm có xuất hiện, mặt nhân vật có đồng nhất, chữ rác trong clip, phụ đề chồng | Không chặn được lỗi người xem thấy ngay |
| F3 | Điểm viral bị ghi đè | AI chấm /10, QA ghi đè /100 vào cùng ô `viral_score` | Mất điểm biên tập của AI |

### G. Gắn đúng sản phẩm và link

| # | Vấn đề | Bằng chứng | Tác động |
|---|---|---|---|
| G1 | Còn ghép sai do trùng chữ chung | "máy xay mini" → **Cáp sạc Micro USB** (độ liên quan 79,8); "Đỗ Mỹ Linh" → móc khoá gấu bông; "Trần Quyết Chiến" (bida) → găng tay gym; "tiền" → ổ cắm du lịch | Video gắn sai sản phẩm → mất uy tín, không có chuyển đổi |
| G2 | AI chỉ lọc lại 5 trend mỗi lượt | `trend_rerank_topics=5`; các trend còn lại không có dòng "AI:" trong lý do | Sản phẩm ghép sai ở trend thứ 6 trở đi không bị loại |
| G3 | Góc nối trend → sản phẩm khiên cưỡng | "lịch thi đấu U23" → loa bluetooth; "cộng IELTS" → tai nghe | Trend nóng nhưng ý định mua thấp (32–35); video dễ giống quảng cáo ép |
| G4 | Link chuẩn ở nhánh review | Caption có `s.shopee.vn` + câu ghi rõ liên kết tiếp thị; viral gắn sau bằng nút **Gắn sản phẩm** | Điểm tốt, giữ nguyên |

### H. Hiệu suất và vòng học

| # | Vấn đề | Bằng chứng | Tác động |
|---|---|---|---|
| H1 | `performance_learning` không được gọi | Không có chỗ nào trong workflow/worker dùng; bảng `video_performance` trống | Không học được hook, độ dài hay giờ đăng nào hiệu quả |
| H2 | Không có nguồn số liệu | Chưa lấy Insights Reels (token thiếu quyền `read_insights`); chuyển đổi Shopee chưa có (không Open API, chưa nhập CSV báo cáo) | Không đo được "viral" và hoa hồng theo video |

### I. Mã nguồn và chuẩn hoá

- Khoảng 30 file sửa và 40 file mới **chưa commit**; ruff báo 25 lỗi.
- Hai hệ prompt (B2); heuristic không dùng (B3); `music.py` 775 dòng gộp nhiều việc: tìm bài, tải, phân tích, chấm, chọn.
- Profile proxy Muse đã được thêm vào: rủi ro bị khoá tài khoản Muse (đã cảnh báo trước đó).
- Gọi AI chậm: viết lại kịch bản khoảng 4–6 phút; phân tích trend khoảng 6,5 phút; nhiều job bị quá 180 s.

---

## 4. Đề xuất ưu tiên cho bước 3

| Ưu tiên | Việc | Giải quyết |
|---|---|---|
| **P1** | Chế độ **"UGC chân thực"** làm mặc định: prompt Muse kiểu quay điện thoại cầm tay, ánh sáng tự nhiên, khung hình không hoàn hảo, ngôi thứ nhất; cho nhân vật **nói thoại bằng tiếng Muse** (câu trong ngoặc kép) với lựa chọn TTS khi cần | A1, A2, A5 |
| **P1** | **Ảnh tham chiếu bắt buộc** cho nhân vật và sản phẩm: tạo character sheet trước mọi storyboard có người; dùng khung cuối cảnh trước làm ảnh đầu cảnh sau; với review, chỉ dùng ảnh sản phẩm có quyền, không có thì không cho Muse vẽ sản phẩm | C1, C2, D4 |
| **P1** | **Một lớp chữ**: hook 0–2 s, rồi chỉ phụ đề, bỏ nhãn IN HOA và emoji; sửa phụ đề chồng; canh lời đọc vừa thời lượng clip (rút thoại hoặc chia cảnh) | A3, E1, E5 |
| **P1** | **QA thật**: AI xem khung hình (mặt nhân vật, sản phẩm, chữ rác), đo thời lượng/nhịp; bỏ điểm nền; tách `ai_review_score` /10 với `qa_score` /100 | F1–F3 |
| **P2** | **Hợp nhất một hệ prompt** (storyboard v2), xoá heuristic cứng; prompt Muse gọn ≤1.500 ký tự theo khung: chủ thể, hành động, bối cảnh, camera, khoá nhân vật; bỏ chuỗi nội bộ | B2, B3, D1, D2 |
| **P2** | **Thời lượng theo mục tiêu**: viral 15–30 s, review 20–40 s; giới hạn số shot theo thời lượng | B4, D3 |
| **P2** | **2–3 hook A/B** cho mỗi video (cùng thân video, khác 3 giây đầu); bỏ ví dụ hook trong prompt; danh sách cấm mở đầu "Ủa/Khoan" lặp lại | A4, B1 |
| **P2** | **Nhạc**: thư viện có giấy phép rõ ràng, lưu nguồn bài vào manifest, chọn theo mood thật và không lặp bài giữa các video gần nhau; bỏ việc tự đọc trang Mixkit nếu điều khoản không cho phép | E3, E4, E6 |
| **P2** | **Ghép sản phẩm chặt hơn**: AI xác nhận mọi cặp trend ↔ sản phẩm (không chỉ 5 trend); bỏ qua trend có ý định mua dưới 40 với nhánh review | G1–G3 |
| **P3** | **Vòng học**: lấy Insights Reels + nhập CSV chuyển đổi Shopee → nối `performance_learning` vào prompt (hook, độ dài, giờ đăng hiệu quả) | H1, H2 |
| **P3** | Giọng theo nhánh/nhân vật; hashtag tiếng Việt, lọc tên nội bộ | A5, A6 |
| **P3** | Chuẩn hoá mã nguồn: commit theo cụm, sửa 25 lỗi ruff, tách `music.py`, tăng timeout hoặc giảm độ dài prompt AI, xem lại profile proxy Muse | I |

---

## 5. Gợi ý cách đo sau nâng cấp

- Chạy cùng 5 brief (2 viral, 2 review, 1 series) trước và sau nâng cấp. So sánh:
  - QA thị giác: đồng nhất nhân vật, sản phẩm đúng, không phụ đề chồng;
  - thời lượng;
  - số lớp chữ;
  - độ dài prompt;
  - số bài nhạc khác nhau.
- Sau khi đăng: tỉ lệ giữ người xem 3 giây và tỉ lệ xem hết từ Insights; click và đơn hàng theo Sub_id của link.

---

## 6. Lỗi chức năng từ bước 1 (nhắc lại)

1. Xoá video nhưng file còn trong inbox → **Quét inbox** làm video sống lại và chạy AI lại.
2. Kịch bản dài vượt timeout 180 s; video kẹt "AI chưa sẵn sàng", không tự chạy lại.
3. AI trả JSON hỏng → video dừng hẳn, không sửa hay hỏi lại.
4. Ô điểm viral dùng 2 thang (/10 và /100).
5. QA cho qua video 5,3 s và cộng điểm nhạc không có thật.
6. Bảng tình trạng báo "0 bài nhạc" dù nhạc lấy từ Mixkit.
7. Thêm trend trống → lỗi 500.
8. Sửa clip không tồn tại hoặc nhập CSV rỗng vẫn báo thành công.
9. Trang Kho sản phẩm và trạng thái đồng bộ chậm 3–5 s (gọi Task Scheduler mỗi lần mở).
