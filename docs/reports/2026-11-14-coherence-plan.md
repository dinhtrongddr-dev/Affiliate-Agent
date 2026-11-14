# Tự đánh giá và kế hoạch sửa: nội dung liền mạch, giọng liền mạch, hình liền mạch (14/11/2026)

**Phản hồi của chủ kênh sau khi xem các video hoàn thiện:**
- Nội dung khó hiểu, rời rạc, người xem phải tự nối, mất cảm xúc, không cô đọng.
- Giọng giật từng đoạn, như AI đọc đúng thời điểm chứ không liền mạch. Cần ưu tiên giọng đầy đủ rồi cắt video theo giọng.
- Chuyển cảnh không mượt, như ghép các video không liên quan, thiếu chuyên nghiệp.
- Hỏi: cơ chế lấy khung cuối 0,3 s làm đầu video sau còn không, có hiệu quả không?

**Bước này chỉ đánh giá và lập kế hoạch, chưa sửa code.**

---

## 1. Đo trên các video thật (không đoán)

### 1.1 Giọng chỉ phủ 48–85% thời lượng, im lặng 2–3 s sau mỗi câu

| Video | Thời lượng | % thời gian có giọng | Im lặng sau câu, từng cảnh (s) |
|---|---|---|---|
| Kiến thức "Rễ cây mọc ngược" | 38,6 s | **48%** | 3,1 · 2,9 · 2,8 · 3,1 · 2,9 · 2,7 · 1,5 · 2,3 |
| Chó con "Bông" | 21,6 s | 53% | 2,2 · 1,0 · 2,4 · 1,9 · 1,2 · 1,7 |
| Series Suri | 31,2 s | 60% | 0,7 · 2,7 · 1,2 · 2,8 · 2,6 · 0,9 · 1,3 · 0,4 |
| Review Cocoon | 28,7 s | 73% | 0,8 · 1,4 · 2,3 · 1,8 · 0,4 · 0,4 · 0,7 |
| Viral "bố tết tóc" | 25,2 s | 85% | 0,5 · 0,4 · 0,4 · 1,4 · 1,1 |

**Nguyên nhân:** AI đặt sẵn thời lượng mỗi cảnh (4–6 s) rồi viết một câu ngắn (khoảng 2 s) cho cảnh đó. Khâu dựng phát câu ở đầu cảnh, phần còn lại của cảnh im lặng. Luật "số từ ≤ thời lượng × 3" chỉ chặn câu quá dài, không chặn câu quá ngắn. Mỗi câu lại được đọc **riêng lẻ**, nên ngữ điệu bắt đầu lại từ đầu sau mỗi câu. Đó là cảm giác "giật từng đoạn, đọc đúng thời điểm".

### 1.2 Lời thoại là các câu rời, có khi 2 giọng cho cùng một người dẫn

Video kiến thức, đọc liền 8 câu: "Rễ cây sao lại mọc lên trời? / Nhìn như ống nhỏ cắm ngược khắp bãi bùn. / Thật ra, đây là rễ thở của cây mắm. / Chúng mọc từ rễ ngang nằm dưới bùn. / Lỗ nhỏ trên rễ cho không khí đi vào. / …"

**Nguyên nhân:**
- Storyboard được viết **theo cảnh trước, lời sau**: mỗi cảnh một câu, nên các câu như danh sách sự kiện, thiếu câu nối ("vì", "nên", "thế nên"), thiếu lý do người xem cần quan tâm, và không có một ý đọng lại.
- Câu đầu do "creator" đọc, các câu sau do "narrator" đọc, mỗi vai một giọng VieNeu, nên người xem nghe như 2 người.

### 1.3 Hình: các cảnh ghép nhau không liên quan

Độ giống (SSIM, 0–1) giữa khung cuối clip trước và khung đầu clip sau:

| Cách tạo clip | SSIM ở chỗ nối |
|---|---|
| **Có nối khung** (job cũ chạy chuỗi đủ 8 clip) | **0,56–0,96**, trung bình khoảng 0,73 |
| Tạo **độc lập** (video kiến thức vừa làm) | **0,14–0,19** |

Hiện mỗi cảnh là một clip Muse riêng, bối cảnh, góc máy và màu đều khác. Khâu dựng nối bằng cắt gần như cứng (0,08 s), không có chuyển cảnh theo ý nghĩa. Tính năng "một clip 10 s cho 2 lần cắt" (`reuse_of`) có sẵn nhưng **0 cảnh** dùng tới.

### 1.4 Trả lời câu hỏi về cơ chế khung cuối 0,3 s

- **Còn trong code.** Mỗi clip tải về được trích khung ở 0,3 s trước khi hết (`continuity_<id>.jpg`). Clip sau, nếu phụ thuộc clip trước, gửi khung này làm ảnh đầu kèm lời dặn "nối tiếp từ khung này".
- **Thực tế đang tắt.** Sau commit T2, nó chỉ chạy khi AI đánh dấu cảnh là `continuous`, và **0 trên 34 cảnh** gần đây được đánh dấu.
- **Có hiệu quả ở khâu tạo clip:** Muse bám theo khung đầu, SSIM 0,73 so với 0,17.
- **Nhưng khâu dựng phá mất hiệu quả đó.** Khâu dựng lấy "đoạn chuyển động rõ nhất" ở **giữa** mỗi clip (thường bắt đầu từ giây 0,25 rồi cắt sau 4–5 s), chứ không dùng **đuôi** clip trước nối với **đầu** clip sau. Khung đã khớp bị cắt bỏ, nên dù tạo clip có nối, video thành phẩm vẫn nhảy cảnh.
- **Chi phí khi bật cho cả video:** các clip phải chạy lần lượt, 8 clip mất 30–40 phút. Đó là lý do T2 đã tắt nó.

**Kết luận:** cơ chế đúng hướng nhưng đang dùng sai chỗ. Cần bật lại **theo từng chuỗi cảnh cùng bối cảnh**, và khâu dựng phải **giữ đúng đuôi–đầu** ở chỗ nối.

### 1.5 VieNeu đọc liền cả đoạn được

Đoạn 81 từ, đọc một lần cả đoạn: ra 29,7 s giọng, tự ngắt nghỉ ở dấu câu (12 chỗ nghỉ tự nhiên), mất 73 s xử lý trên CPU. Đọc từng câu rồi ghép: 28,7 s, mất 61 s. Cả hai cách đều làm được **trong lúc chờ Muse**, nên không kéo dài tổng thời gian.

---

## 2. Tự đánh giá: lỗi nằm ở thiết kế, không phải tham số

Pipeline hiện tại đi theo thứ tự **cảnh → lời → giọng → dựng**, mỗi cảnh là một đơn vị độc lập: tự có thời lượng, tự có câu, tự có clip. Vì vậy:
- lời đi theo hình, nên thành chuỗi câu rời;
- giọng đi theo cảnh, nên có khoảng lặng sau mỗi câu;
- hình là các clip độc lập, nên nhảy cảnh.

Video chuyên nghiệp đi ngược lại: **lời là xương sống**. Viết kịch bản liền mạch trước, thu giọng liền mạch, rồi **cắt hình theo giọng**. Mỗi câu có hình minh hoạ đúng điều đang nói ("nói gì cho xem nấy"), và các cảnh cùng bối cảnh được quay như một chuỗi liên tục.

Bộ chấm QA hiện tại không đo các lỗi bạn thấy: không đo khoảng lặng của giọng, không đo độ dễ hiểu, không đo độ mượt chỗ nối. Vì vậy video vẫn đạt QA 85–90.

---

## 3. Kế hoạch sửa

### S1. Giọng là trục, hình cắt theo giọng (ưu tiên 1, chạy trên máy, chắc chắn có lợi)

| # | Việc | Chi tiết |
|---|---|---|
| S1.1 | **Làm toàn bộ lời đọc trước** | Ngay khi có kịch bản (trong lúc chờ Muse), đọc từng câu của toàn video rồi ghép thành **một track giọng liền**. Cắt khoảng lặng thừa ở đầu và cuối mỗi câu; chèn nghỉ tự nhiên: 0,2–0,3 s giữa các câu, 0,45 s khi chuyển ý. Chỉ để khoảng lặng dài hơn khi kịch bản đánh dấu chủ ý (`pause`) cho khoảnh khắc cảm xúc |
| S1.2 | **Thời lượng cảnh lấy từ giọng** | Mỗi nhịp (beat) dài đúng bằng câu thoại của nó cộng khoảng nghỉ. Bỏ thời lượng cố định do AI đặt. Tổng video = track giọng + 0,8 s đuôi. Clip Muse 10 s đủ cho 1–2 nhịp |
| S1.3 | **Cắt kiểu J-cut** | Hình đổi trước câu mới 0,15–0,25 s (tiếng dẫn hình), như cách dựng chuyên nghiệp; không cắt đúng lúc câu bắt đầu |
| S1.4 | **Một người dẫn, một giọng** | Video giải thích, phim tài liệu, review: chỉ một người dẫn ("creator" và "narrator" là một). Nhiều giọng chỉ dùng cho định dạng kể chuyện hoặc series có thoại nhân vật |
| S1.5 | **Phụ đề theo câu** | Thời gian phụ đề lấy từ track giọng thật, chia cụm 3–5 từ trong mỗi câu |
| S1.6 | Nâng cấp sau (tuỳ kết quả nghe thử) | Đọc liền cả đoạn (ngữ điệu tự nhiên hơn) rồi căn thời gian bằng faster-whisper chạy CPU, để vẫn biết chính xác ranh giới câu. Chỉ làm nếu nghe thử S1.1 vẫn thấy rời |

**Xong khi:** giọng phủ ≥ 85% thời lượng; không còn khoảng lặng > 0,8 s ngoài chủ ý; nghe thử 3 video thấy liền mạch.

### S2. Kịch bản viết trước, liền mạch, có một ý đọng lại (ưu tiên 2)

| # | Việc | Chi tiết |
|---|---|---|
| S2.1 | **Hai bước trong cùng một lần gọi AI** | (a) **Lời kịch bản liền** như một đoạn văn nói: hook đặt câu hỏi hoặc lời hứa → bối cảnh → diễn biến có câu nối ("vì", "nên", "hoá ra") → **payoff trả lời đúng hook** → câu chốt. Ngân sách khoảng **2,6 từ/giây** (đo thực tế VieNeu 2,7 từ/s). (b) Sau đó mới **chia thành nhịp**, mỗi nhịp gồm một hoặc vài câu và một cảnh **minh hoạ đúng điều đang nói** |
| S2.2 | **Một ý đọng lại** | Kịch bản có trường `takeaway` (một câu người xem nhớ). Mọi câu phải phục vụ takeaway; bỏ chi tiết lan man (ví dụ video rễ cây có 8 sự kiện ngang nhau, nên chỉ giữ 3–4 ý dẫn tới một điều bất ngờ) |
| S2.3 | **Thử "người xem lạ"** | Một lần gọi AI ngắn (khoảng 20 s) đọc **chỉ phần lời**, không xem hình, rồi trả lời: video nói gì trong một câu? câu nào thiếu ngữ cảnh? hook có được trả lời không? Lệch takeaway hoặc có câu thiếu ngữ cảnh thì sửa một lần |
| S2.4 | **Luật kiểm trong code** | Có `takeaway`; hook được trả lời ở cuối; mỗi câu 6–16 từ; mật độ từ của nhịp phù hợp thời lượng hình; kịch bản có ≥ 2 câu nối ý |

### S3. Hình liền mạch theo chuỗi cảnh (ưu tiên 3)

| # | Việc | Chi tiết |
|---|---|---|
| S3.1 | **Chuỗi cảnh (sequence)** | Các nhịp liên tiếp cùng bối cảnh, cùng nhân vật gom thành một chuỗi. Mỗi chuỗi mở bằng một cảnh toàn (establishing). Mỗi video khoảng 2–4 chuỗi |
| S3.2 | **Một take cho nhiều nhịp** | Một clip Muse 10 s mô tả **chuỗi hành động liền** của 2 nhịp; khâu dựng cắt nhịp 1 rồi nhịp 2 **theo thứ tự thời gian** trong cùng take. Như vậy liền mạch thật, và số clip giảm khoảng 40–50% |
| S3.3 | **Bật lại nối khung trong chuỗi** | Take sau trong cùng chuỗi dùng khung cuối của take trước (đã đo SSIM 0,73). Các chuỗi khác nhau chạy song song. Ước tính thời gian Muse bằng khoảng 2 lần thời gian một clip (6–12 phút), không phải 30–40 phút |
| S3.4 | **Dựng giữ đúng chỗ nối** | Trong chuỗi: lấy **đuôi** take trước tới hết và **đầu** take sau từ giây 0, đặt chỗ nối vào ranh giới câu. Bỏ "đoạn chuyển động rõ nhất" cho các cảnh có nối |
| S3.5 | **Chuyển cảnh theo ý nghĩa** | Trong chuỗi: cắt trên hành động hoặc nối liền. Đổi bối cảnh hoặc thời gian: hoà tan 0,3–0,5 s. Không chọn kiểu chuyển cảnh ngẫu nhiên |
| S3.6 | **Đồng màu cả video** | Cân màu và độ sáng từng clip về clip tham chiếu đầu chuỗi (ffmpeg `eq`/`colorlevels` theo thống kê), cộng một lớp màu nhẹ chung cho cả video. Đo độ lệch màu trung bình giữa hai cảnh liền nhau trước và sau khi cân |
| S3.7 | **Lời dặn trong prompt Muse** | Cùng phía máy quay, cùng thời điểm trong ngày, cùng thời tiết trong một chuỗi; nhắc lại bối cảnh của chuỗi trong mỗi take |

### S4. Âm thanh hoàn thiện

- Nhạc nền chạy liền cả video, giảm âm khi có giọng (đã có); tiếng nền phòng lấp các khoảng nghỉ.
- Âm thanh hoà tan qua chỗ cắt bằng `acrossfade` khớp thời gian chuyển hình.
- Tuỳ chọn: tiếng động nhẹ cho hành động chính (bước chân, nước, tiếng "ting" nồi cơm). Để sau.

### S5. QA đo đúng các lỗi bạn thấy

| Chỉ số mới | Ngưỡng | Cách đo |
|---|---|---|
| Giọng phủ thời lượng | ≥ 85% | Từ track giọng thật |
| Khoảng lặng dài nhất ngoài chủ ý | ≤ 0,8 s | Từ track giọng thật |
| Độ mượt chỗ nối trong chuỗi | SSIM ≥ 0,5 | So khung hai bên mỗi chỗ cắt |
| Độ dễ hiểu | Đạt bài thử "người xem lạ" | S2.3 |
| Hình khớp lời | ≥ 7/10 | QA xem hình nhận thêm câu đang đọc ở mỗi khung để chấm |

Không đạt thì cổng chất lượng xử lý: giọng hoặc dựng lỗi thì dựng lại; lời khó hiểu thì sửa kịch bản một lần; chỗ nối lỗi thì tạo lại take đó.

### S6. Đo trước và sau

Làm lại đúng 5 chủ đề cũ: rễ cây, chó con, Suri, Cocoon, bố tết tóc. So các chỉ số ở mục S5 trước và sau khi sửa, kèm **bạn nghe và xem thử 3 video** để chốt. Lưu kết quả vào `docs/reports/bench-coherence-<ngày>.json`.

---

## 4. Thứ tự và ước lượng

| Bước | Ước lượng | Tác động lên điều bạn thấy |
|---|---|---|
| S1 Giọng là trục | 1–1,5 ngày | Hết giật cục và hết khoảng lặng; tác động lớn nhất, ít rủi ro |
| S2 Kịch bản liền mạch | 1 ngày | Dễ hiểu, cô đọng, có cảm xúc |
| S3 Hình theo chuỗi cảnh | 1,5–2 ngày | Hết cảm giác ghép các clip không liên quan |
| S4 Âm thanh | 0,5 ngày | Trơn tru hơn ở chỗ chuyển |
| S5 + S6 QA và đo | 0,5–1 ngày | Ngăn lỗi quay lại; có số chứng minh |

**Thời gian sản xuất:**
- S1 không làm chậm thêm vì giọng làm trong lúc chờ Muse.
- S2 thêm khoảng 20 s cho bài thử "người xem lạ".
- S3 có nối khung trong chuỗi, khâu Muse ước tăng từ khoảng 9,5 lên 10–13 phút. Bù lại, số clip giảm nhờ một take dùng cho nhiều nhịp.

**Rủi ro:**
- Muse có thể không giữ được hành động liền trong cùng take như mô tả. S3.2 sẽ đo bằng SSIM và QA xem hình; không đạt thì quay về mỗi nhịp một clip có nối khung.
- Đọc từng câu rồi ghép vẫn có thể nghe hơi rời ở ranh giới câu. Có phương án S1.6 (đọc liền cả đoạn rồi căn thời gian) để nâng cấp.
