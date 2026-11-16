# Tham khảo 4 repo tạo video UGC / review sản phẩm (15/11/2026)

Đã đọc mã nguồn và tài liệu (không chạy) của:

| Repo | Là gì | Giá trị tham khảo |
|---|---|---|
| norahe0304-art/30x-product-to-ugc | Skill cho Claude Code: link sản phẩm → video review UGC 9:16 (avatar HeyGen + quay màn hình sản phẩm + phụ đề + sticker), có bộ "luật nghề" và bảng đối chiếu với quảng cáo thắng | **Cao nhất** — phần luật (`rules/`) là tinh hoa |
| Anil-matcha/Open-AI-UGC | Web Next.js bọc API muapi (Veo 3.1, Seedance 2, Grok Video…): người dùng tự viết prompt + tối đa 7 ảnh tham chiếu `@image1..7` | Thấp — không có logic kịch bản/storyboard |
| harry0703/MoneyPrinterTurbo | Chủ đề → LLM viết kịch bản → từ khoá → ghép video stock (Pexels/Pixabay) + TTS + phụ đề + nhạc | Trung bình — kỹ thuật ghép b-roll, chuyển cảnh, phụ đề |
| Dinesh-DLanzer/ai-studio | App "producer" (Tara) phỏng vấn → `Story.md` → ảnh nhân vật/bối cảnh chuẩn → từng clip 4s → editor 36 chuyển cảnh | Cao — mẫu storyboard từng shot, checklist QA clip bằng AI nhìn |

## 1. Kịch bản

**30x-product-to-ugc** — nhịp review chuẩn (giọng người thật, ngôi thứ nhất, nói một hơi):
1. **Hook ≤ 1,5 giây**: phản ứng thật / nỗi đau / khẳng định táo bạo. Cấm "Giới thiệu sản phẩm X".
2. **Nỗi đau** — trước đây tệ ở đâu.
3. **"Thế là mình thử X"** — điểm xoay.
4. **Cho nó làm việc** — gọi tên 2–4 tính năng, *mỗi tính năng = một cảnh bằng chứng*.
5. **Kết quả** — trạng thái sau.
6. **CTA mềm có lời (6–8 giây)** — "link ở giỏ hàng". Cấm thẻ kết thúc im lặng.

Mỗi cảnh phải có một **nhiệm vụ hình ảnh**: *proof* (chứng minh là thật) / *mechanism* (hoạt động thế nào) / *consequence* (vì sao quan trọng) / *action* (bước tiếp theo). Cảnh không có nhiệm vụ = cảnh độn → xoá.

**"Product truth" trước khi viết**: ghi ra sản phẩm là gì, cho ai, 2–4 tính năng sẽ nói, mỗi tính năng quay cảnh gì để chứng minh. Cấm bịa tính năng.

**ai-studio**: luồng `Hook → Pain → Contrast → Solution → Result → CTA`; mỗi shot ghi Mục đích / Hình / Máy quay / Hành động / Lời (ai nói, trong hay ngoài khung hình) / Chữ trên màn hình (thêm khi dựng, **không** để AI vẽ chữ) / Ảnh tham chiếu / Prompt video.

**MoneyPrinterTurbo**: prompt kịch bản ngắn gọn ("vào thẳng vấn đề, không 'chào mừng các bạn', không markdown, cùng ngôn ngữ chủ đề"); sinh **từ khoá b-roll theo đúng thứ tự lời đọc** (`match_script_order`) để cảnh khớp câu.

## 2. Storyboard sản phẩm, bối cảnh, màu sắc

- **Tính thật > sự chau chuốt** (30x): UGC chết khi trông như quảng cáo. Cầm tay, ánh sáng đời thường, góc lệch; "nếu thêm thứ gì làm nó *thiết kế* hơn thì bỏ".
- **Danh sách đen "AI slop"**: b-roll cyberpunk tối, hạt phát sáng, dòng dữ liệu, laptop lơ lửng, "não AI"; ảnh tĩnh giữ > ~3,5 s; mở đầu giọng thương hiệu; nhạc át lời.
- **Người review giống khách hàng mục tiêu** (mẹ bỉm cho đồ em bé, nữ trẻ cho mỹ phẩm…); **giọng phải khớp mặt** (giới tính/tuổi/giọng vùng); **nhân vật phải cử động** (tay, đi lại, đổi cảnh) — đứng im = giả.
- **Quy tắc nhất quán** (ai-studio): mỗi nhân vật và mỗi bối cảnh có **một ảnh chuẩn**; mọi clip giữ cùng mặt, tóc, quần áo, bối cảnh.
- **Màu**: ai-studio lấy **màu chủ đạo từ logo/ảnh sản phẩm** (`dominant_colors`) rồi ghi vào storyboard làm phong cách màu.
- **Phân vùng khung 9:16** (30x): trên = pill/hook; giữa = sản phẩm/b-roll; **dải phụ đề dưới cùng độc quyền**; người nói thu thành PiP bo góc ở góc dưới khi tới đoạn tính năng. Hai thứ không được chiếm cùng vùng cùng lúc.

## 3. Prompt cho AI tạo video

**ai-studio** (rất gọn, hợp với Muse):
`SUBJECT (cùng mặt/quần áo như khung đầu) / ACTION (một) / CAMERA (một chuyển động) / LIGHTING / STYLE`
- Một shot = một clip **4 s** (6–8 s chỉ khi câu cần). Không bao giờ tạo cả video trong một clip.
- Chữ trên màn hình thêm lúc dựng, không bao giờ để model sinh chữ.
- Claim: thể hiện kết quả bằng hình, không hứa doanh số.

**Open-AI-UGC**: tối đa 7 ảnh tham chiếu, gọi trong prompt bằng `@image1` (mặt), `@image2` (sản phẩm), `@image3` (bối cảnh).

## 4. Dựng, chuyển cảnh, phụ đề, âm thanh

- **Cắt mỗi 1,5–3 s**, không cảnh tĩnh quá 3 s; trong một nhịp vẫn phải động: zoom vào, đổi góc, hiệu ứng chạm, phóng to con số (30x).
- **Khoá hình theo từ** (beat-lock): chuyển cảnh/phụ đề/sticker gắn đúng giây từ đó được nói (Whisper từng từ); **cắt ở khoảng nghỉ giữa câu**, không cắt giữa cụm từ.
- **Sticker là "tiếng mẹ đẻ" của UGC**: chữ hook to ở khung đầu, mũi tên chỉ, khoanh số quan trọng, sao đánh giá, **nhãn giá**, sticker CTA "link ở giỏ hàng" — tối đa 1–2 sticker/khung, một phong cách xuyên suốt.
- **Phụ đề**: 2–3 từ/cụm, từ đang đọc tô vàng, viền trắng, chữ đậm, giữa-dưới; thêm emoji/khối màu "pop". MoneyPrinterTurbo có hiệu ứng **phụ đề nảy lò xo** (scale = 1 − e^(−6p)·cos(2.5πp)).
- **Chuyển cảnh**: MoneyPrinterTurbo dùng fade/slide/zoom/shuffle; ai-studio có 36 kiểu xfade của ffmpeg. 30x: UGC ưa **cắt thẳng nhanh** hơn hiệu ứng.
- **Âm thanh**: nhạc trend làm nền, tự hạ khi có lời; chuẩn âm lượng `I=-16:TP=-1.5:LRA=11`; sổ nhạc chống lặp bài; kiểm tra nhạc miễn phí không có watermark giọng nói.
- **Thời lượng** quảng cáo thắng: **15–35 s**.

## 5. QA

- **ai-studio — checklist 7 điểm cho mỗi clip AI** (model nhìn trả JSON pass/warn/fail + lỗi theo khung):
  1. chỉ đúng nhân vật dự kiến; 2. mặt/quần áo/bối cảnh khớp khung đầu; 3. không chữ rác; 4. không logo thương hiệu thật; 5. **không khung vỡ/bóng ma/chồng hình, nhất là giây cuối**; 6. không méo mặt/tay; 7. có lời thì thấy miệng người nói.
- **30x — bảng 10 tiêu chí** đối chiếu với ~1.100 quảng cáo thắng (hook, cử động, giọng khớp mặt, phụ đề, sticker, b-roll sản phẩm, nhịp cắt, thời lượng, nhạc, CTA) và sửa lỗ hổng nặng nhất trước.
- **Bài test cuối**: "trộn vào feed TikTok, người thường có thấy ngay là quảng cáo không?" — có thì làm lại.

## 6. So với app hiện tại

| Đã có | Còn thiếu (có thể áp dụng) |
|---|---|
| Phụ đề karaoke, tiêu đề hook, CTA, Ken Burns, duck nhạc, loudnorm | **Lớp sticker** (nhãn giá, sao, mũi tên, khoanh, CTA "giỏ hàng") |
| Prompt Muse có cấu trúc (camera/who/action/setting/light/speech/product/avoid) | Ràng buộc **một hành động + một chuyển động máy** mỗi shot; danh sách đen "AI slop" trong phần avoid |
| Ảnh nhân vật chuẩn, chuỗi cảnh liền mạch (khung cuối 0,3 s) | **Checklist QA 7 điểm cho từng clip** (đặc biệt "giây cuối bị bóng ma" — ảnh hưởng trực tiếp tới khung nối 0,3 s) |
| Kịch bản viết trước, takeaway, viewer test, facts | **Nhiệm vụ hình ảnh mỗi cảnh** (proof/mechanism/consequence/action) và "product truth" 2–4 tính năng + cảnh chứng minh |
| Hook A/B | Hook **≤ 1,5 s** + **chữ hook to ở khung đầu**; **A/B theo người review** (persona khác nhau) |
| QA giọng phủ, khoảng lặng, SSIM chỗ nối | Chỉ số **cảnh tĩnh dài nhất ≤ 3 s**; chấm theo **bảng 10 tiêu chí** |
| Pexels | Từ khoá b-roll **theo thứ tự lời đọc** |
| — | **Màu chủ đạo từ ảnh sản phẩm** → màu phụ đề/sticker/nhãn giá |
| — | **Phụ đề nảy (spring pop)**, emoji điểm xuyết |
| — | **Nhãn nhỏ "nội dung AI"** ở góc thay vì nói trong lời |

## 7. Đề xuất ưu tiên

1. **Lớp sticker + chữ hook khung đầu + sticker CTA "giỏ hàng"** — tác động lớn nhất tới cảm giác "UGC thật" và chuyển đổi; thuần ffmpeg/ASS, không tốn quota.
2. **Checklist QA 7 điểm cho từng clip Muse** — gắn vào QA hình hiện có; cảnh fail tự tạo lại (đã có cơ chế FIX).
3. **Storyboard: nhiệm vụ hình ảnh + product truth + một hành động/một chuyển động máy + danh sách đen AI slop** — sửa prompt `creative.py` / `muse_prompt.py`.
4. **Nhịp cắt ≤ 3 s + chỉ số cảnh tĩnh dài nhất** trong QA flow.
5. **Màu từ ảnh sản phẩm**, phụ đề nảy, emoji, A/B theo persona — đánh bóng.

Không áp dụng: HeyGen avatar (trả phí, app đã dùng Muse), quét Meta Ad Library (cần tuân thủ điều khoản riêng), hệ thống credits/Stripe.
