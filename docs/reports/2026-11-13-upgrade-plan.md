# Bước 3: kế hoạch nâng cấp (13/11/2026)

**Dựa trên:** [báo cáo chất lượng bước 2](2026-11-13-content-quality-review.md) (điểm yếu A–I, 9 lỗi bước 1) và đọc lại mã nguồn hiện tại, gồm cả phần chưa commit.
**Hiện trạng đo được hôm nay:** 364 test đều qua, nhưng mất 3 phút 43 giây (riêng nạp VieNeu TTS 35 s; có test gọi thẳng trang Mixkit qua mạng); ruff báo 25 lỗi import thừa; `workflow.py` dài 1.562 dòng, `web/app.py` 1.004 dòng, `editor/music.py` 775 dòng.
**Bước này chỉ lập kế hoạch, chưa sửa code.**

---

## 0. Mục tiêu và cách đo

Ba mục tiêu:
1. **Video trông như clip đời thường**, nhân vật và sản phẩm đồng bộ, không na ná nhau.
2. **Gắn đúng sản phẩm**, và link đo được đơn hàng theo từng video.
3. **Quy trình nhanh, ổn định**, mã nguồn gọn và dễ bảo trì.

| Chỉ số | Hiện tại (bằng chứng bước 2) | Mục tiêu |
|---|---|---|
| Độ dài prompt Muse mỗi shot | 8.144–9.014 ký tự | ≤ 1.500 |
| Cảnh có người được gửi ảnh tham chiếu nhân vật | 0/8 (video "mẹ") | 100% |
| Lớp chữ hiện cùng lúc | 3 (hook, nhãn IN HOA, karaoke) | 1 (hook 0–2 s, sau đó chỉ phụ đề) |
| Phụ đề chồng nhau | Có | 0, kiểm tự động |
| Thời lượng video viral | 48,8 s | 15–30 s |
| Số bài nhạc khác nhau trên 10 video | 1 (Mixkit Track 738) | ≥ 6, không lặp trong 5 video liên tiếp |
| Điểm QA | Luôn 90+ (điểm nền 78,5) | Điểm thật từ kiểm hình và số đo; video lỗi phải trượt |
| Ghép đúng trend ↔ sản phẩm | Ít nhất 4/11 trend ghép sai | ≥ 90% chính xác trên bộ 40 cặp có nhãn |
| Số lần gọi AI cho một video nhánh mới | 4 (ý tưởng, storyboard, chấm, chấm lại) | ≤ 3, còn 2 khi đạt ngay |
| Thời gian từ brief tới storyboard | 4–6 phút, hay quá 180 s | Trung vị ≤ 3 phút; không job nào kẹt mà không tự thử lại |
| Số liệu sau khi đăng | Chưa có | 100% video đã đăng có tỉ lệ giữ 3 s, xem hết, chia sẻ, click, đơn theo Sub_id |

---

## 1. Nguyên tắc

- **Giữ các điểm mạnh:** 3 nhánh sáng tạo, storyboard dạng hợp đồng, khoá trang phục, luật trung thực dữ liệu, chuẩn kỹ thuật đầu ra, lọc an toàn trend.
- **Mỗi việc đều có test và được đo trước/sau** trên bộ đo chuẩn (P0.3). Thay đổi lớn bật bằng cờ cấu hình, và chỉ thành mặc định khi số đo tốt hơn.
- **Chân thực không có nghĩa là giấu AI.** Mục tiêu là nội dung gần gũi, không phải đánh lừa người xem:
  - Video AI vẫn được khai báo nhãn "nội dung AI" khi đăng; Meta yêu cầu tự khai báo video chân thực do AI tạo.
  - Nhánh review không tự nhận đã dùng sản phẩm khi chủ kênh chưa xác nhận trải nghiệm.
- **Chi tiêu Muse có giới hạn:** mỗi lần thử Muse báo trước số clip (tổng dự kiến ở mục 9).
- **Ràng buộc cũ giữ nguyên:**
  - Không tự đăng khi chưa duyệt.
  - Không dùng proxy/VPN để lách giới hạn vùng của Muse.
  - Không cào dữ liệu Shopee/TikTok.
  - Không đăng lại video của người khác.
- **Mỗi giai đoạn kết thúc giống nhau:** chạy bộ đo chuẩn, báo cáo trước/sau, commit, rồi dừng chờ bạn duyệt.

---

## 2. Lộ trình tổng quan

| Giai đoạn | Nội dung | Ước lượng | Cần trước |
|---|---|---|---|
| **0. Nền móng** | Commit phần đang dở, sửa 9 lỗi bước 1, dựng bộ đo chuẩn | 1–2 ngày | Bạn đồng ý cách commit (mục 11) |
| **1. Một hệ prompt, chân thực, đồng bộ** | Storyboard v2, bộ biên dịch prompt Muse, phong cách UGC, giọng, ảnh tham chiếu, một lớp chữ, QA thật | 4–5 ngày | Giai đoạn 0; thử nghiệm Muse nói tiếng Việt |
| **2. Viral và đúng link** | Thời lượng, hook A/B, chống lặp, nhạc có giấy phép, ghép sản phẩm chặt, kiểm link | 3–4 ngày | Giai đoạn 1; bạn chọn nguồn nhạc |
| **3. Vòng học** | Insights Reels, CSV hoa hồng, nối `performance_learning`, giọng theo nhân vật | 2–3 ngày, rồi chờ đủ dữ liệu | Token có quyền `read_insights`; file CSV hoa hồng |
| **Xuyên suốt** | Hiệu năng (mục 7), chuẩn hoá mã nguồn (mục 8) | Làm dần trong từng giai đoạn | — |

**Đổi thứ tự so với báo cáo bước 2:** việc "hợp nhất một hệ prompt" (P2 trong báo cáo) được **đưa lên đầu giai đoạn 1**. Phong cách UGC, ảnh tham chiếu và một lớp chữ đều sửa vào chỗ sinh prompt. Nếu làm trước khi hợp nhất, mỗi thay đổi phải sửa hai lần trên hai hệ prompt.

---

## 3. Giai đoạn 0: nền móng

### P0.1 Commit và dọn mã

| Việc | Chi tiết | Xong khi |
|---|---|---|
| Commit theo cụm | Khoảng 30 file sửa và 40 file mới chưa commit, chia thành các cụm: ① ruff `--fix` 25 import thừa; ② AddLive data API kèm tài liệu; ③ nhánh sáng tạo (`creation_branches`, `storyboard_support`, `creation_context`, `creative_series`); ④ hậu kỳ (nhạc Mixkit, VieNeu TTS, phụ đề, render); ⑤ đồng bộ Shopee theo lịch (`shopee_schedule`, `auto_sync`, `scripts/sync-shopee.py`); ⑥ videogen (engine, continuity chain, `muse_profiles` tuỳ quyết định ở mục 11); ⑦ giao diện; ⑧ báo cáo | Mỗi commit chạy test qua; `git status` sạch |
| Lệnh kiểm tra chung | `scripts/check.ps1`: ruff cộng bộ test nhanh, chạy trước mỗi commit | 0 lỗi ruff |
| Profile proxy Muse | `videogen/muse_profiles.py` thuộc nhóm lách vùng, có rủi ro bị khoá tài khoản Muse. Mình **không phát triển tiếp** phần này; đề xuất gỡ bỏ hoặc khoá tắt mặc định | Theo quyết định của bạn |

### P0.2 Sửa 9 lỗi của bước 1

| # | Lỗi | Cách sửa | Ở đâu |
|---|---|---|---|
| 1 | Xoá video xong, **Quét inbox** làm nó sống lại | Khi xoá job, chuyển file nguồn sang `inbox/_deleted/`, đồng thời ghi hash file vào danh sách bỏ qua; watcher bỏ qua các hash này | `watcher.py`, hàm xoá job trong `web/app.py` |
| 2 | Quá thời gian chờ AI 180 s, job kẹt "AI chưa sẵn sàng" | Mỗi loại lệnh gọi AI có timeout và mức suy nghĩ riêng (H1); thêm tác vụ định kỳ tự thử lại `WAITING_FOR_AI_SERVICE` sau 5, 15, 45 phút, tối đa 3 lần, sau đó báo người dùng | `worker.py`, `workflow._ai_status`, `settings.py` |
| 3 | AI trả JSON hỏng làm job dừng hẳn | Sửa JSON tại chỗ (bỏ markdown, dấu phẩy thừa, cắt đúng object), không được thì gọi thêm một lần AI ngắn "sửa JSON này", hỏng tiếp mới báo lỗi | `workflow._parse_json_reply` |
| 4 | Ô `viral_score` lẫn hai thang /10 và /100 | Tách 2 cột: `ai_review_score` (/10, điểm biên tập AI) và `qa_score` (/100, điểm QA); chuyển dữ liệu cũ sang; giao diện hiện cả hai | `db.py`, `workflow.py:989`, các template |
| 5 | QA cho qua video 5,3 s và cộng điểm nhạc không có thật | Giải quyết hẳn ở P1.7; trước mắt chặn ngay video lệch thời lượng mục tiêu hoặc có `music: null` | `quality.py` |
| 6 | Bảng tình trạng báo "0 bài nhạc" | Đếm theo nguồn nhạc đang dùng thật (thư viện hoặc nguồn ngoài) | `web/app.py`, dashboard |
| 7 | Thêm trend với tiêu đề trống gây lỗi 500 | Kiểm tra đầu vào, trả 400 kèm thông báo tiếng Việt | `web/app.py` `/api/trends` |
| 8 | Sửa clip không tồn tại hoặc nhập CSV rỗng vẫn báo thành công | Trả 404 cho clip không có, 400 cho CSV rỗng | `clips.py`, `shopee_intelligence/importer.py` |
| 9 | Trang Kho sản phẩm và trạng thái đồng bộ chậm 3–5 s | Lưu đệm trạng thái Task Scheduler 60 s, làm mới ở nền | `shopee_schedule.py` |

Mỗi lỗi có một test chống tái phát.

### P0.3 Bộ đo chuẩn

- **`scripts/bench_content.py`** chạy 5 brief cố định tới bước storyboard và prompt Muse đã biên dịch.
  - Năm brief: 2 viral ("Ai là người hiểu mẹ nhất", "con chó chạy ngoài đồng"), 2 review (2 sản phẩm thật trong kho), 1 series Suri.
  - Dùng nhà cung cấp Muse giả, nên **không tốn quota**.
  - Số đo:
    - độ dài prompt Muse;
    - số từ bị cấm ("cinematic", "photorealistic", "studio");
    - tổng thời lượng;
    - số từ thoại trên mỗi giây;
    - số ảnh tham chiếu mỗi cảnh;
    - số lớp chữ;
    - số lần gọi AI và thời gian từng lần.
- **Bộ 40 cặp trend ↔ sản phẩm có nhãn đúng/sai**, lấy từ 11 trend thật và kho 301 sản phẩm. Mình gán nhãn, bạn duyệt lại khoảng 15 phút. Bộ này dùng để đo độ chính xác của bộ ghép.
- Kết quả lưu tại `docs/reports/bench-<ngày>.json` để so sánh qua từng giai đoạn. **Kết quả đầu tiên được chạy trước khi sửa bất cứ thứ gì**, làm mốc.

---

## 4. Giai đoạn 1: một hệ prompt, chân thực, đồng bộ (P1)

### P1.1 Storyboard v2 và một hệ prompt duy nhất

Giải quyết B2, B3, B5.

- **`creative/schema.py`** mô tả hợp đồng dữ liệu bằng pydantic: code kiểm tra cấu trúc thay vì tin vào AI.
  - `Character`: id, tên, vai, tuổi, ngoại hình, trang phục, giọng.
  - `Scene`: id, `group` (nhóm cùng địa điểm, dùng để nối cảnh), thời lượng, nguồn hình, `cast` (các id nhân vật có mặt), `reference_ids`, `product_visible`, shot, lời thoại, phụ đề.
  - `Shot`: hành động (một nhịp), bối cảnh, máy quay, ánh sáng, cảm xúc, câu thoại kèm người nói, âm thanh.
  - `Storyboard`: `version: 2`, nhánh, `style_profile`, `voice_mode`, thời lượng mục tiêu, 3 hook, nhân vật, các cảnh, caption, hashtag, `affiliate_keywords`.
- **Code tự kiểm, không nhờ AI:**
  - tổng thời lượng nằm trong khoảng mục tiêu của nhánh;
  - lời thoại mỗi cảnh không quá thời lượng × 3,5 từ/giây;
  - nhân vật trong `cast` phải có thật;
  - số cảnh generate không vượt ngân sách.

  Sai thì gọi **một lần sửa ngắn** kèm danh sách lỗi, không dừng job.
- **Một bộ prompt cho tất cả:** mỗi nhánh có một cấu hình `BRANCH_CONFIG` (mục đích, thời lượng, giọng mặc định, chính sách CTA), dùng chung một prompt ý tưởng, một prompt storyboard, một prompt chấm.
  - Chế độ video base: chuyển thành `product_review` có thêm các đoạn video gốc.
  - Chế độ trend cũ: chuyển thành `viral_creator` hoặc `product_review`.
- **Xoá:**
  - `trend_plan_prompt`, `review_prompt`, `base_plan_prompt` (sau khi chuyển xong);
  - các heuristic giả AI `evaluate_creative_idea`, `generate_hook_variations`, `determine_duration_strategy` cùng test của chúng;
  - các câu ví dụ trong `HOOK_FORMULAS` (giữ phần mô tả công thức, bỏ câu mẫu để AI khỏi chép).
- **Tương thích ngược:** `plan_state.json` bản v1 được chuyển sang v2 khi đọc, nên các job cũ vẫn dựng lại được.
- **Xong khi:** chỉ còn một họ prompt; test cũ qua hoặc đã thay bằng test mới; job v1 dựng lại thành công.

### P1.2 Bộ biên dịch prompt Muse

Giải quyết D1, D2, D3.

- **`creative/compile_muse.py`:** AI chỉ điền các trường shot ngắn; code ghép thành prompt **≤ 1.500 ký tự** theo thứ tự cố định:
  1. kiểu quay và máy quay (theo phong cách ở P1.3);
  2. ai xuất hiện: mỏ neo ngoại hình **chỉ của nhân vật trong cảnh này**, không đưa cả danh sách nhân vật;
  3. hành động, đúng một nhịp;
  4. bối cảnh Việt Nam cụ thể;
  5. ánh sáng;
  6. câu thoại trong ngoặc kép, nếu `voice_mode` cho Muse nói;
  7. nối cảnh ("bắt đầu đúng từ khung hình đính kèm 1"), nếu có;
  8. ràng buộc ngắn: không chữ trên hình, không logo, không phụ đề.
- **Bỏ các chuỗi nội bộ:**
  - Dòng "Reference manifest digest" chuyển vào khoá chống trùng (`task_id`), không nằm trong prompt nữa; tính năng chống gửi trùng giữ nguyên.
  - Bỏ câu "Dialogue is script context only…".
- **Thời lượng:** Muse luôn trả clip khoảng 10 s. Vì vậy sẽ yêu cầu đúng 10 s ("một cảnh quay liền 10 giây"), còn khâu dựng tự chọn đoạn 3–6 s đẹp nhất (P1.6, P2.1).
- **Test chụp mẫu (snapshot):** kiểm độ dài, không có từ bị cấm, đủ mỏ neo nhân vật.
- **Xong khi:** 5 brief của bộ đo đều cho prompt ≤ 1.500 ký tự và không chứa "cinematic/photorealistic/studio".

### P1.3 Phong cách "UGC chân thực" làm mặc định

Giải quyết A1.

- **`creative/style_profiles.py`** có 3 phong cách:
  - `ugc_phone`: mặc định cho viral và review;
  - `sitcom_bright`: cho series Suri;
  - `cinematic`: chỉ dùng khi chọn tay.
- **`ugc_phone` mô tả:**
  - quay bằng điện thoại cầm tay, rung nhẹ tự nhiên;
  - ngang tầm mắt, hoặc selfie một tay;
  - ánh sáng cửa sổ hoặc bóng đèn vàng trong nhà;
  - nhà cửa, đường phố Việt Nam có đồ đạc thật;
  - khung hình không hoàn hảo, da thật.

  Cấm: chỉnh màu điện ảnh, đèn studio, quay chậm, loá ống kính, flycam.
- **Khâu dựng làm theo phong cách:**
  - `ugc_phone` dùng cắt cứng là chính, bỏ các chuyển cảnh kiểu mẫu (`circleopen`, `slideup`…);
  - không zoom/pan giả lên clip AI.
- Bỏ "Photorealistic" khỏi `character_prompt` và `STYLE` trong `videogen/engine.py`.
- **Xong khi:** điểm "giống clip điện thoại" của QA hình (P1.7) ≥ 7/10 trên 3 video đo chuẩn. Đây là giả thuyết, phải đo bằng clip Muse thật.

### P1.4 Giọng và thoại

Giải quyết A2.

- **Thử nghiệm trước, tốn 4 clip Muse:** cho nhân vật nói một câu tiếng Việt trong ngoặc kép (2 cảnh × 2 cách viết). Chấm phát âm, khẩu hình, và giọng có giữ được giữa các clip không.
- **Ba chế độ `voice_mode`:**
  - `muse_dialogue`: nhân vật tự nói bằng tiếng của Muse;
  - `first_person_tts`: TTS đọc ở ngôi thứ nhất ("mình");
  - `narrator_tts`: kiểu hiện tại, người dẫn chuyện.
- **Chọn mặc định theo kết quả thử:** đạt thì dùng `muse_dialogue` cho thoại nhân vật; không đạt thì dùng `first_person_tts`.
- **Tiếng gốc của Muse:** giữ tiếng nền phòng (room tone) ở mức thấp dưới giọng đọc thay vì tắt hẳn, để nghe như quay thật.
- **Trung thực:** ở nhánh review, chỉ nói "mình dùng" khi `experience.basis=owner_experience`. Nếu không, chuyển sang "mình tìm hiểu", "mình xem thông tin".
- **Xong khi:** có báo cáo kết quả thử, đã chọn chế độ mặc định, và các video viral đo chuẩn không còn giọng kể ngôi thứ ba.

### P1.5 Ảnh tham chiếu bắt buộc và nối cảnh

Giải quyết C1, C2, C3, D4.

- **Ảnh mẫu nhân vật cho storyboard:**
  - Trong `_plan_storyboard`, tạo trước tối đa 3 ảnh nhân vật chính (task ảnh) theo phong cách `ugc_phone`; các cảnh phụ thuộc vào ảnh này.
  - Hiện chỉ luồng cũ `plan_job` có ảnh nhân vật, còn luồng storyboard thì không, nên cả 8 cảnh đều có `refs=[]`.
  - Gán ảnh **theo `scene.cast`**. Hiện `attach_references` gán mặc định mọi ảnh cho mọi cảnh (`storyboard_support.py:93`).
- **Bước "Duyệt nhân vật"** (tuỳ chọn): job dừng cho bạn xem ảnh nhân vật, tạo lại tốn 1 ảnh. Nên bật cho series, tuỳ ý với video viral lẻ.
- **Series Suri:** tạo và duyệt ảnh gia đình Suri một lần, lưu vào `assets/creative_series` với quyền `owned` (ảnh do app tạo), nâng mẫu lên v2 có ảnh.
- **Bảng quyền ảnh sản phẩm:**
  - Ảnh có quyền (owned/licensed/official): gửi làm tham chiếu cho cảnh có sản phẩm.
  - Ảnh quyền `unknown` (AddLive): **Muse không được vẽ sản phẩm**. Cảnh sản phẩm phải dùng ảnh/clip bạn tự chụp; app hiện danh sách việc cần làm ("Thêm 2–3 ảnh/clip sản phẩm tự chụp"). Clip Muse chỉ quay tình huống hoặc vấn đề, không có sản phẩm.
  - Code chặn: cảnh có `product_visible=true` thì phải có ảnh tham chiếu sản phẩm.
- **Nối cảnh theo nhóm:** `videogen_continuity_chain` (đã có trong code chưa commit, đang tắt) lấy khung cuối cảnh trước làm ảnh đầu cảnh sau.
  - Nối cả chuỗi thì 8 cảnh phải chạy lần lượt, mất 30–40 phút.
  - Nên chỉ **nối trong cùng `scene.group`** (cùng địa điểm, liên tiếp), còn các nhóm chạy song song.
- **Xong khi:**
  - 100% cảnh có người được gửi ảnh nhân vật;
  - điểm đồng nhất khuôn mặt của QA hình ≥ 7/10;
  - video review không còn cảnh sản phẩm do Muse tự vẽ.

### P1.6 Một lớp chữ, sửa phụ đề, khớp lời với hình

Giải quyết A3, E1, E2, E5.

- **`editor/subtitles.build_ass` chỉ còn 2 lớp:**
  - Hook: 0–2 s, chữ thường có hoa đầu câu, không emoji, ≤ 7 từ;
  - phụ đề.

  Bỏ lớp nhãn (Label). CTA chỉ là dòng phụ đề cuối của nhánh review, không làm thành thẻ.
- **Phụ đề không tràn cảnh:**
  - mỗi dòng kết thúc trước cảnh sau 0,05 s;
  - tối đa 2 dòng, mỗi dòng 3–5 từ.
- **Bỏ karaoke từng từ theo mặc định**, vì mốc thời gian chỉ là ước lượng; dùng phụ đề theo cụm từ. Nếu sau này có công cụ căn giọng thật thì bật lại.
- **Khớp lời với hình ngay từ storyboard:**
  - Code ước lượng thời lượng lời đọc trước khi gọi Muse; dài quá thì gọi một lần sửa ngắn để rút gọn.
  - Khi dựng, nếu lời vẫn dài hơn, **dùng thêm phần thật của clip 10 s** thay cho đóng băng khung cuối (`tpad`). Đóng băng chỉ là cách cuối cùng, tối đa 0,3 s.
- **Xong khi:** test đọc file ASS không thấy chồng dòng; video đo chuẩn không có đoạn đứng hình quá 0,3 s.

### P1.7 QA thật: xem hình và đo số

Giải quyết F1, F2, F3.

- **`qa/visual.py`:** mỗi video gọi một lần AI xem ảnh.
  - Ảnh gửi kèm: khung hook, 1 khung mỗi cảnh, ảnh nhân vật, ảnh sản phẩm tham chiếu.
  - Kết quả là JSON, mỗi mục chấm 0–10 kèm lỗi:
    - đồng nhất khuôn mặt;
    - đồng nhất trang phục;
    - sản phẩm khớp ảnh thật;
    - lỗi hình (tay méo, chữ rác);
    - phụ đề đọc được;
    - độ "giống clip điện thoại";
    - sức hút của khung hook.
- **`qa/score.py`:** `qa_score` /100, **không có điểm nền, không cộng theo từ khoá**.
  - Điểm hình: 50.
  - Số đo: 30, gồm thời lượng đúng mục tiêu, 1 lớp chữ, không chồng phụ đề, lời vừa cảnh, có nhạc có nguồn rõ, độ to, không khung đen hay đứng hình.
  - Biên tập: 20, quy đổi từ `ai_review_score`.
- **Ba mức kết luận:**
  - **CHẶN:** sản phẩm sai hoặc thiếu sản phẩm ở nhánh review; phụ đề chồng; thời lượng lệch quá 30%.
  - **CẢNH BÁO.**
  - **ĐẠT.**
- **Nếu gọi AI xem ảnh lỗi**, ghi "CHƯA KIỂM HÌNH". Không bao giờ tự cho đạt.
- **Xong khi:**
  - video 5,3 s và video "mẹ" 49 s không còn được ĐẠT;
  - bạn chấm tay 5 video và điểm của bạn đi cùng chiều với `qa_score`.

---

## 5. Giai đoạn 2: viral và đúng link (P2)

### P2.1 Thời lượng theo mục tiêu

Giải quyết B4, D3.

- **Khoảng thời lượng trong `BRANCH_CONFIG`, kiểm bằng schema:**

  | Nhánh | Thời lượng | Số cảnh |
  |---|---|---|
  | `viral_creator` | 15–30 s | 4–6 |
  | `product_review` | 20–40 s | 5–8 |
  | `template_series` | 20–35 s | — |
- **Nhịp cắt:** hook ≤ 2 s; mỗi cảnh 2–5 s, cắt từ clip Muse 10 s.
- **Chọn đoạn cắt:** khâu dựng lấy đoạn có chuyển động rõ nhất trong clip, đo bằng ffmpeg.

### P2.2 Hook A/B

Giải quyết A4, B1.

- **Storyboard có 3 hook khác loại** (tò mò, đồng cảm, POV/bất ngờ) dùng chung thân video.
- **Dựng tiết kiệm:** thân video dựng một lần; 3 đoạn mở đầu (≤ 3 s) dựng riêng rồi ghép, chỉ mã hoá lại phần hook. Kết quả là 3 file để chọn trên trang duyệt.
- **Cấu hình:** `AA_VARIANTS` (hiện không có tác dụng) được nối vào đây, nhận giá trị 1–3.
- **Muse:** hook phụ dùng đoạn khác của clip có sẵn. Chỉ tạo clip mới cho hook phụ khi bạn bật.
- **Giới hạn:** Facebook Reels không có A/B gốc qua API. Bạn chọn bản nào đăng ở đâu (ví dụ bản A lên Reels, bản B lên Shopee Video), giai đoạn 3 sẽ so kết quả.

### P2.3 Chống lặp: bộ nhớ kênh

Giải quyết A4, A6.

- **`creative/memory.py`** lấy 30 video gần nhất: hook, tiêu đề, từ mở đầu, bài nhạc, hashtag. Prompt nhận mục "đã dùng gần đây, tránh lặp".
- **Code kiểm:**
  - một từ mở đầu xuất hiện ở ≥ 3 trong 10 video gần nhất thì yêu cầu sửa;
  - có danh sách mở đầu cấm ("Ủa", "Khoan", "Xin chào", "Hôm nay mình"), trừ khi chính trend dùng câu đó.
- **Hashtag:**
  - tiếng Việt, 3–5 cái;
  - chặn tên nội bộ (`#ViralCreator`, tên nhánh);
  - chặn tiếng Anh, trừ tên thương hiệu hoặc tên trend.

### P2.4 Nhạc có giấy phép, đúng tâm trạng, không lặp

Giải quyết E3, E4, E6.

- **Nguồn nhạc (bạn chọn, mục 11):**
  - **(a)** Thư viện `assets/music` kèm `tracks.json` (tên bài, tác giả, nguồn, giấy phép, tâm trạng, nhịp, năng lượng) do bạn cung cấp. Mình khuyến nghị cách này.
  - **(b)** Giữ Mixkit sau khi bạn kiểm điều khoản cho video thương mại (affiliate là thương mại). Tải theo cách trang cho phép và ghi đủ thông tin bài.
- **Chọn bài:**
  - theo tâm trạng của từng đoạn truyện (cảm động thì piano nhẹ, hài thì nhạc nảy);
  - không dùng lại bài đã dùng trong 5 video hoặc 7 ngày gần nhất;
  - ghi `music: {tên, tác giả, nguồn, giấy phép}` vào manifest;
  - dashboard hiện đúng nguồn.
- **Tách `music.py` (775 dòng)** thành `library`, `analyze`, `select`, và `sources/mixkit` nếu giữ Mixkit.
- **Xong khi:** 10 lần dựng của bộ đo dùng ≥ 6 bài khác nhau, và QA xác nhận nhạc hợp tâm trạng.

### P2.5 Ghép sản phẩm chặt hơn và kiểm link trước khi duyệt

Giải quyết G1, G2, G3.

- **AI xác nhận mọi cặp ứng viên**, không chỉ 5 trend như hiện nay (`trend_rerank_topics=5`):
  - xét mọi cặp có độ liên quan ≥ `MIN_RELEVANCE`;
  - gom 15 cặp một lần gọi, mức suy nghĩ thường;
  - lưu kết quả 7 ngày theo (trend, sản phẩm).
- **Luật cứng:**
  - trend là tên người hoặc sự kiện mà phân tích ý định không nêu sản phẩm thì không gắn sản phẩm, chỉ làm video viral (giải quyết lỗi Đỗ Mỹ Linh → móc khoá);
  - từ chung chung ("mini", "tiền", "du lịch", "bộ") không đủ để ghép;
  - nhóm hàng của ý định phải hợp với nhóm hàng của sản phẩm.
- **Ý định mua < 40** thì không làm review, chỉ gợi ý nhánh viral. Ví dụ: "lịch thi đấu U23" → loa, "cộng IELTS" → tai nghe.
- **Nhánh viral:** `_related_affiliates` (`workflow.py:527`) hiện gọi `match_products` mà không qua AI xác nhận; phải đi qua cùng bước xác nhận.
- **Kiểm link trước READY:**
  - link thuộc đúng sản phẩm trong kho;
  - là link affiliate hợp lệ của bạn;
  - **có Sub_id = mã video**: `create_short_link` đã làm việc này, nhưng link nhập từ CSV thường không có Sub_id. Khi thiếu, app cảnh báo "không đo được đơn theo video" và hướng dẫn tạo link có Sub_id;
  - tên sản phẩm trong caption khớp sản phẩm đã gắn;
  - lời review chỉ nêu dữ kiện có trong `product_context`.
- **Xong khi:** đạt ≥ 90% chính xác trên bộ 40 cặp có nhãn, và có test chống tái phát cho 4 lỗi ghép sai đã thấy.

---

## 6. Giai đoạn 3: vòng học (P3)

| Việc | Chi tiết | Cần bạn |
|---|---|---|
| **P3.1 Insights Reels** | `meta_reels` lấy lượt xem, thời gian xem trung bình, chia sẻ, bình luận, tiếp cận của reel đã đăng lúc 1 giờ, 24 giờ, 72 giờ và 7 ngày (tác vụ định kỳ), lưu vào `video_performance` | Tự tạo token có quyền `read_insights` trong Meta (mình không cấp quyền thay bạn) |
| **P3.2 Hoa hồng theo video** | Nhập CSV báo cáo Shopee Affiliate, ghép theo Sub_id = mã video, ra click, đơn, hoa hồng mỗi video. Hiện `analytics.py` mới làm qua Open API, cách này chưa dùng được | Tải CSV báo cáo từ trang Shopee Affiliate |
| **P3.3 Nối `performance_learning`** | Module có sẵn nhưng chưa nơi nào gọi. Nối để ghi số liệu, phân tích theo nhánh, loại hook, độ dài, chế độ giọng, tâm trạng nhạc, giờ đăng, nhóm hàng. Khi đủ ≥ 10 video có số liệu 72 giờ thì đưa "điều hiệu quả trên kênh này" vào prompt ý tưởng và storyboard (đưa sớm hơn dễ học sai). Thêm trang "Hiệu quả" và kết quả hook A/B | Đăng đều để có dữ liệu |
| **P3.4 Giọng theo nhánh và nhân vật** | Mỗi nhân vật series một giọng VieNeu cố định; review dùng một giọng kênh; tốc độ theo nhịp; từ điển phát âm tên thương hiệu | Chọn giọng |

---

## 7. Hiệu năng (xuyên suốt)

| # | Việc | Hiệu quả dự kiến |
|---|---|---|
| H1 | **Mức suy nghĩ và timeout theo từng loại lệnh gọi:** ý tưởng dùng mức thường (120 s); storyboard dùng `extended` (300 s); chấm dùng mức thường (120 s); sửa lỗi dùng mức thường và ít token. Hiện mọi lệnh gọi đều dùng `extended` với 180 s | Ít timeout hơn, tổng thời gian giảm |
| H2 | **Bước chấm trả về danh sách sửa (patch)**, không viết lại cả storyboard (`max_tokens` 7.000 giảm còn khoảng 1.500), và tự chấm luôn bản đã sửa. Bỏ lần chấm lại riêng (hiện bị cắt ở 600 token) | 4 lần gọi còn 3, hoặc 2 khi đạt ngay; hết lỗi B5 |
| H3 | Tự thử lại job chờ AI theo thời gian giãn dần (P0.2 #2) | Không còn job kẹt |
| H4 | Sửa JSON tại chỗ, rồi một lần gọi sửa (P0.2 #3) | Không mất cả job vì một dấu phẩy |
| H5 | **Muse:** nối cảnh theo nhóm và chạy song song giữa các nhóm; kiểm ngân sách trước khi gửi; nhà cung cấp giả cho bộ đo | Video 6 cảnh không còn phải chờ 6 lượt nối nhau |
| H6 | **Dựng:** thân video dựng một lần cho 3 hook; lưu đệm TTS theo (câu, giọng, tốc độ) | Thêm hook A/B mà gần như không thêm thời gian |
| H7 | Lưu đệm trạng thái đồng bộ Shopee (P0.2 #9) | Trang kho mở dưới 0,5 s |
| H8 | **Chia bộ test thành nhanh và chậm** (đánh dấu test có TTS, ffmpeg, mạng; đăng ký mark `network` đang bị pytest cảnh báo; test không được gọi mạng thật theo mặc định) | Bộ nhanh dưới 90 s cho mỗi commit; bộ đủ chạy trước khi kết thúc giai đoạn |
| H9 | Lọc lỗi `WinError 10054` của asyncio khỏi log (63 dòng rác) | Log dễ đọc |

---

## 8. Chuẩn hoá mã nguồn

**Cấu trúc đích** (tách dần, mỗi lần tách có test giữ hành vi):

```
services/agent_core/
  creative/            # thay creative.py
    schema.py          # Storyboard v2 (pydantic) + chuyển v1 → v2
    branches.py        # BRANCH_CONFIG (gộp creation_branches)
    prompts.py         # một prompt ý tưởng / storyboard / chấm / sửa
    rules.py           # luật dùng chung, không câu mẫu
    style_profiles.py  # ugc_phone, sitcom_bright, cinematic
    compile_muse.py    # prompt Muse ≤ 1.500 ký tự
    memory.py          # chống lặp hook/nhạc/hashtag
  qa/                  # thay quality.py
    technical.py       # validate_* hiện có
    clip.py            # check_generated_clip
    visual.py          # QA xem hình (mới)
    score.py           # qa_score không điểm nền (mới)
  editor/music/        # tách music.py
  workflow/            # tách workflow.py 1.562 dòng
    pipeline.py        # _process, _create_and_render
    content.py         # caption, hashtag, link
    publish.py         # approve / publish_*
    review_page.html   # trang duyệt (hiện là HTML viết trong Python)
  web/routers/         # tách web/app.py 1.004 dòng: jobs, trends, catalog, settings, videogen
```

**Quy ước:**
- ruff (thêm luật sắp xếp import) chạy qua `scripts/check.ps1` trước mỗi commit.
- Kiểu dữ liệu rõ cho hàm công khai.
- Mọi JSON từ AI đi qua schema.
- Prompt nằm ở một chỗ và có test chụp mẫu.
- Không câu mẫu cứng trong prompt.
- Cờ cấu hình mới ghi đủ trong `.env.example`: `AA_STYLE_PROFILE`, `AA_VOICE_MODE`, `AA_VISUAL_QA`, `AA_VARIANTS`, `AA_MUSIC_SOURCE`.
- Xoá cấu hình không còn tác dụng.

**Tài liệu:** cập nhật `docs/creation-branches.md` và `docs/muse-video-engine.md`, thêm `docs/content-pipeline.md` (một sơ đồ cho toàn bộ chuỗi), và cập nhật bảng theo dõi trong `STATUS.md`.

---

## 9. Chi phí Muse và AI dự kiến

| Mục | Muse (clip/ảnh) | Ghi chú |
|---|---|---|
| Bộ đo chuẩn (P0.3) | 0 | Dùng nhà cung cấp giả |
| Thử Muse nói tiếng Việt (P1.4) | 4 clip | 2 cảnh × 2 cách viết |
| Ảnh nhân vật (P1.5) | 3 ảnh viral, 3 ảnh gia đình Suri, tạo lại tối đa khoảng 4 | Lưu dùng lại, chỉ tốn một lần |
| Kiểm thử cuối giai đoạn 1 | Khoảng 9 clip | 1 video viral 5 cảnh, 1 video review 4 cảnh |
| Kiểm thử giai đoạn 2 | ≤ 10 clip | Hook phụ dùng lại clip có sẵn |
| **Tổng** | **Khoảng 33** | Thấp hơn nhiều mức 200/ngày, nhưng quota tuần của Muse chưa rõ nên sẽ rải ra nhiều ngày |

**AI (ChatGPT2API):**
- Mỗi video thêm 1 lần gọi xem ảnh để QA, nhưng bớt 1–2 lần gọi chấm, nên tổng số lần gọi giảm.
- Bước xác nhận sản phẩm gom 15 cặp mỗi lần gọi và lưu kết quả 7 ngày.

---

## 10. Rủi ro và cách xử lý

| Rủi ro | Cách xử lý |
|---|---|
| Muse nói tiếng Việt kém, khẩu hình lệch | Lùi về `first_person_tts`, giữ tiếng nền thật của Muse |
| Có ảnh tham chiếu mà Muse vẫn đổi mặt | QA hình bắt lỗi, cho tạo lại riêng cảnh lỗi (đã có cơ chế thử lại 1 lần); giới hạn số lần tạo lại |
| Nối cảnh làm chậm | Chỉ nối trong nhóm cùng địa điểm, chạy song song giữa các nhóm |
| AI xem ảnh chậm hoặc lỗi | Ghi "CHƯA KIỂM HÌNH" và để bạn duyệt tay; không tự cho đạt |
| Đổi cấu trúc lớn gây lỗi mới | Tách từng bước, có bộ test, bộ đo chuẩn và cờ bật/tắt; job v1 vẫn dựng được |
| Thư viện nhạc có giấy phép cần bạn cung cấp bài | Trong lúc chờ, giữ nguồn hiện tại nhưng không lặp bài và ghi rõ nguồn |
| Insights cần quyền trên Meta | Bạn tự cấp; trước khi có quyền, giai đoạn 3 chỉ chạy phần hoa hồng từ CSV |
| Profile proxy Muse có thể làm tài khoản bị khoá | Đề xuất gỡ hoặc khoá tắt (mục 11) |

---

## 11. Quyết định cần bạn

1. **Thứ tự:** đồng ý đưa "một hệ prompt" lên đầu giai đoạn 1 không?
2. **Commit phần chưa commit** (của bạn hoặc người khác) theo các cụm ở P0.1: đồng ý không?
3. **Ngân sách Muse** khoảng 33 clip/ảnh cho toàn bộ thử nghiệm, rải nhiều ngày: đồng ý không?
4. **Nguồn nhạc:** tự cung cấp thư viện có giấy phép (khuyến nghị), hay giữ Mixkit sau khi bạn kiểm điều khoản?
5. **Ảnh sản phẩm cho nhánh review:** bạn tự chụp 2–3 ảnh/clip mỗi sản phẩm (khuyến nghị), hay chấp nhận video review không có cận cảnh sản phẩm?
6. **Profile proxy Muse:** gỡ bỏ hay khoá tắt mặc định?
7. **Giai đoạn 3:** khi tới lúc, bạn tạo token có `read_insights` và tải CSV báo cáo hoa hồng.

---

## 12. Đối chiếu điểm yếu với việc làm

| Điểm yếu (bước 2) | Việc |
|---|---|
| A1 phong cách điện ảnh | P1.2, P1.3 |
| A2 giọng kể ngôi thứ ba | P1.4 |
| A3 ba lớp chữ | P1.6 |
| A4 hook rập khuôn | P1.1 (bỏ câu mẫu), P2.2, P2.3 |
| A5 một giọng | P3.4 |
| A6 hashtag nội bộ/tiếng Anh | P2.3 |
| B1 không A/B | P2.2 |
| B2 hai hệ prompt, B3 heuristic giả AI | P1.1 |
| B4 thời lượng | P2.1 |
| B5 chấm lại bị cắt | H2 |
| C1 không ảnh nhân vật, C2 không nối cảnh, C3 Suri chưa có ảnh | P1.5 |
| D1 prompt dài, D2 chuỗi nội bộ, D3 lệch thời lượng | P1.2, P2.1 |
| D4 Muse tự vẽ sản phẩm | P1.5 |
| E1 phụ đề chồng, E2 karaoke lệch, E5 đóng băng khung | P1.6 |
| E3 một bài nhạc, E4 tự đọc trang Mixkit, E6 báo 0 bài | P2.4, P0.2 #6 |
| F1 điểm nền, F2 không xem hình, F3 ghi đè điểm | P1.7, P0.2 #4 |
| G1 ghép sai, G2 chỉ lọc 5 trend, G3 góc khiên cưỡng | P2.5 |
| H1 chưa nối học, H2 không có số liệu | P3.1–P3.3 |
| I mã nguồn | P0.1, mục 7, mục 8 |
| 9 lỗi bước 1 | P0.2 |

---

## 13. Trạng thái thực hiện (cập nhật 14/11/2026)

**Giai đoạn 0: xong.**
- Commit theo cụm, ruff 0 lỗi, sửa 9 lỗi bước 1.
- Có bộ đo chuẩn (`scripts/bench_content.py`).

**Giai đoạn 1: xong**, kiểm bằng 2 video Muse thật và bộ đo chuẩn.

Đã có:
- storyboard v2 (người nói theo câu, nhân vật theo cảnh, nhóm địa điểm);
- một hệ prompt duy nhất;
- prompt Muse ≤ 1.500 ký tự, phong cách quay điện thoại;
- Muse tự tạo ảnh nhân vật trước;
- nối cảnh theo nhóm địa điểm;
- dàn giọng VieNeu theo người nói, có nâng cao độ cho giọng trẻ em;
- lồng tiếng theo nhịp miệng;
- một lớp chữ, phụ đề không chồng;
- QA xem hình thật;
- nhạc Mixkit theo tâm trạng, không lặp.

| Chỉ số (5 brief) | Mốc 13/11 | Sau giai đoạn 1 |
|---|---|---|
| Prompt Muse dài nhất | 8.922 ký tự | 1.500 |
| Brief có từ cấm trong prompt | 5/5 | 0/5 |
| Cảnh có ảnh tham chiếu | 0/38 | 29/42 (cảnh không có người thì không cần) |
| Thời lượng | 38–56 s | 23–36 s |
| Cảnh lời dài hơn hình | 10 | 0 |

**Video thật** "Ai là người hiểu mẹ nhất":
- dài 27,5 s (trước là 48,8 s), có 4 ảnh nhân vật;
- QA hình: khuôn mặt 9/10, trang phục 10/10, giống quay điện thoại 9/10.

**Còn lại cho giai đoạn 2:**
- ghép sản phẩm: độ chính xác 66,7%;
- hook A/B;
- lời review vẫn dè dặt khi chưa có trải nghiệm thật;
- series Suri chưa chạy thử với Muse thật.

**Giai đoạn 2: xong (14/11/2026).**

| Việc | Kết quả |
|---|---|
| Ghép sản phẩm (P2.5) | AI xác nhận mọi cặp, gom 15 cặp một lần gọi, lưu 7 ngày. Ngưỡng ý định mua 40; dưới ngưỡng chỉ giữ sản phẩm đúng đối tượng (fit ≥ 75). Từ chung chung không còn tự khớp. Bộ 36 cặp có nhãn: độ chính xác **66,7% → 100%** (giữ đúng 15 cặp, không cặp sai nào) |
| Kiểm link (P2.5) | Chặn duyệt khi video review thiếu link, link không hợp lệ hoặc link của sản phẩm khác. Báo khi link từ kho chưa có Sub_id |
| Hook A/B (P2.2) | Storyboard sinh thêm 2 hook. Thân video dựng một lần, mỗi bản chỉ làm lại phần chữ. Trang video có trình phát và nút "Dùng bản này" |
| Chống lặp (P2.3) | Hook gần đây được đưa vào prompt; hashtag 3–5 cái, bỏ tên nội bộ và tiếng Anh chung chung; nhạc không lặp trong 8 video gần nhất |
| Thời lượng và cắt (P2.1) | Viral 15–30 s, review 20–40 s; cảnh không nói lấy đoạn chuyển động rõ nhất của clip 10 s |
| Review tự nhiên | Không còn từ hệ thống; tối đa một câu rào đón |
| Con vật chính | Có ảnh tham chiếu riêng như nhân vật người |

Video thật:
- **Chó con "Bông":** QA 91,8; ngoại hình giữ đúng 9/10.
- **Review cây lau nhà:** QA 88,5; sản phẩm khớp ảnh thật 10/10.
