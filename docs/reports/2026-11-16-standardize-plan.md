# Plan: tách prompt 3 nhánh, làm app nhẹ, timeline tiến độ, Series nói tiếng Việt bằng Muse, chuẩn hoá app, rồi push

## Context

Chủ kênh phản ánh 4 vấn đề:
1. Video kém và không đồng nhất vì prompt storyboard của 3 nhánh trộn lẫn ("nồi lẩu").
2. App chạy rất nặng, tab Chrome chiếm nhiều RAM.
3. Không biết job đang ở bước nào, còn bao lâu, được bao nhiêu %.
4. Muốn Series dùng giọng tiếng Việt của Muse, nói đúng kịch bản, đúng giọng nhân vật, đồng nhất hình và giọng qua các tập. Chủ kênh có nghe nói tạo trong cùng một đoạn chat thì Muse tự đồng nhất.

Kèm theo là các việc đã đồng ý trước đó: commit phần Muse health, làm mục 1–4 của báo cáo UGC (mục 5 để sau). Cuối cùng chuẩn hoá app và push lên GitHub.

Chủ kênh chốt về giọng Series: **làm mọi cách để Muse nói đúng kịch bản và khớp giọng nhân vật.**

### Những gì đã đo và đọc được

**Ba nhánh** (`creation_branches.py:9-13`): `product_review`, `viral_creator`, `template_series`.
- Chỉ có **một** prompt storyboard dùng chung cho cả ba: `creative.branch_plan_prompt` (`creative.py:167-327`). Luật từng nhánh được nhét vào bằng if/else. Chỗ trộn lẫn cụ thể:
  - Luật review (xưng "mình", cấm từ hệ thống, `missing_info`) lọt vào cả viral lẫn series (`:279-290`).
  - Prompt sửa `branch_review_prompt` vẫn ép "tối đa 2 cảnh nói" lên series hội thoại (`:338`), làm hỏng lời thoại.
  - Ý đồ series luôn là "hài gia đình" theo kiểu Suri (`:138-142`) và ghi "photorealistic", trong khi Suri là chibi 3D, và chính prompt đó cấm chữ này (`:302`).
  - Lời dặn máy quay mâu thuẫn nhau ("không cầm tay" ở `:293`, "app thêm kiểu điện thoại" ở `:302`).
  - Hai định nghĩa "hội thoại" khác nhau: `is_dialogue` và `_cast_plan_voices` (`workflow.py:714`).
  - `DURATION_TARGETS` bị chép ở 2 nơi (`quality.py:167`).
- Lỗi ở prompt gửi Muse:
  - Khung nối tiếp chèn vào vị trí 1 làm số thứ tự "attachment N" lệch đi một (`engine.py:474-482` so với `muse_prompt.py:64-95`).
  - Cảnh "không có sản phẩm" lại lấy ảnh sản phẩm làm khung đầu (`storyboard_support.py:94-100` cùng câu bọc trong gateway, `muse_gateway/app.py:988-995`).
- Vòng FIX tạo lại clip với **đúng prompt cũ**, không đưa lỗi QA vào (`engine.py:673-690`).
- **Lỗi tiếng:** `.env` đặt `AA_KEEP_SOURCE_AUDIO=true`, nên `render.py:175,191` trộn tiếng gốc của clip Muse ở mức 0,35. Kết quả là tiếng Muse nói lung tung nằm dưới giọng VieNeu. Thiết lập này vốn chỉ dành cho "video base".

**Vì sao app nặng.** Đo lúc rảnh: Chrome headless của Muse chỉ khoảng 94 MB. Nguyên nhân chính nằm ở chỗ khác:
- Các trang dashboard, jobs, tasks tự `location.reload()` mỗi 8–10 giây. Chúng chạy suốt cả thời gian chờ Muse hàng chục phút, kể cả khi tab đang ẩn, vì không có chỗ nào kiểm tra `document.hidden`.
- Mỗi lần tải lại, dashboard chạy `health()`: đọc file, quét thư mục nhạc, `SELECT *`.
- Trang job nặng: JSON in đầy đủ, ảnh không lazy-load. Còn `/api/jobs/{id}` trả cả các cột JSON lớn, gọi mỗi 4 giây.
- `/api/muse/profiles` giải mã DPAPI 2 lần và gọi gateway mỗi 30 giây.
- Model VieNeu được nạp sẵn lúc mở app (`web/app.py:148-152`) và chạy bằng CPU i5-4590.
- 3 luồng worker cứ 2 giây lại mở SQLite và lấy khoá ghi, kể cả khi rảnh.
- Gateway chạy vòng 0,6 giây đọc `innerText` trang 1440×2400 trong lúc tạo clip. `tasks.json` (1,1 MB) bị ghi lại toàn bộ ở mỗi bước tiến độ và không bao giờ được dọn.
- Chưa có cửa sổ desktop riêng. App mở bằng `webbrowser.open` (`tray.py:123-132, 205`).

**Tiến độ.** Job chỉ có `status`. Có `timings.json` nhưng chỉ xem được sau khi xong và thiếu mốc `script_start`. Gateway có báo % cho từng clip, nhưng app đọc xong rồi bỏ đi (`providers.py:173`, `engine.py:548`).

**Series và Muse:**
- Gateway mở thread `/thread/new` **mới** cho mỗi clip (`muse_gateway/engine.py:728-731, 786`), đóng context sau mỗi job (`app.py:1222`), đổi tài khoản mỗi job (`:1151`), và dặn Muse bỏ qua lịch sử (`:978-1015`). Vì vậy hiện **không thể** dùng chung chat.
- Về kỹ thuật thì làm được: ghi lại `location.href`, mở lại thread cũ, cố định tài khoản. Nhưng chưa ai kiểm chứng Muse có dùng các lượt trước để giữ nhân vật hay không.
- Gateway nhận được file audio (`engine.py:50-64`), nhưng app chưa bao giờ gửi.
- Lần thử 15/11 (`b616d5b`, rồi `df4ad90`): khớp kịch bản 0/8 câu. Không có báo cáo đo chi tiết. Prompt lúc đó vẫn bị câu bọc tiếng Trung của gateway bao ngoài.

---

## Thứ tự thực hiện

Mỗi giai đoạn có commit riêng, test và ruff phải qua trước khi commit. Push ở cuối.

### GĐ 0. Commit phần Muse health đang dở
- Commit 14 file đang thay đổi (health profile Muse, test 36/36 đã qua).

### GĐ 1. Đo trước khi sửa (baseline)
- Script đo tại `scripts/bench_resources.py` (đọc tiến trình qua `psutil` hoặc `Get-Process`): RAM và CPU của app, gateway, ChatGPT2API, Chrome headless và tab UI, lúc rảnh và lúc có một job đang chạy.
- Lưu kết quả vào `docs/reports/bench-2026-11-16-resources-before.json` để so sánh sau khi sửa.

### GĐ 2. Giảm tải
**Frontend** (`web/static/app.js`, `templates/dashboard.html`, `jobs.html`, `tasks.html`, `job.html`, `static/muse_profiles.js`):
- Bỏ toàn bộ `location.reload()` định kỳ. Thay bằng một hàm poll dùng chung trong `app.js`:
  - chỉ gọi JSON nhẹ, chỉ cập nhật dòng hoặc badge thay đổi;
  - dừng khi `document.hidden`, chạy lại khi `visibilitychange`;
  - giãn dần 4 s → 15 s khi không có gì đổi.
- API nhẹ mới: `/api/jobs/summary?ids=` và `/api/jobs/{id}/progress`, chỉ đọc cột trạng thái và tiến độ. Không còn `SELECT *` kèm JSON lớn.
- `loading="lazy"` cho ảnh keyframe, thumbnail và ảnh sản phẩm. Thu gọn JSON debug vào `<details>` và chỉ tải khi mở ra.
- `Cache-Control: max-age` dài cho file tĩnh có `?v=`.

**Backend:**
- `health()`: cache 60 giây (`web/app.py:206-240`).
- `muse_profiles.list`: cache bản giải mã theo mtime của file (`videogen/muse_profiles.py:126-146`). Trang chỉ poll 60 giây và chỉ khi đang hiển thị.
- Worker (`worker.py:252-273`):
  - các luồng chờ trên `threading.Event`, được đánh thức khi `db.enqueue` thêm việc;
  - lúc rảnh thì ngủ 15 giây thay vì cứ 2 giây lấy khoá SQLite một lần.
- VieNeu:
  - bỏ warmup lúc mở app (`web/app.py:148-152`), chỉ nạp khi job thật sự cần (vẫn trong tiến trình con như `tts.py:91-118`);
  - tự tắt tiến trình con sau 10 phút rảnh.
- Tiến trình nặng (ffmpeg, VieNeu, đánh giá giọng) chạy ở `BELOW_NORMAL_PRIORITY_CLASS` để giao diện luôn mượt. Áp dụng qua `run_ffmpeg` và tiến trình con TTS.

**Gateway** (`services/muse_gateway`):
- Vòng chờ kết quả: 0,6 s → 1,5 s. Thay `document.body.innerText` bằng selector hẹp, chỉ tìm element `hatch-chat-attachment-presentation-*` mới (`engine.py:1199-1265`).
- `tasks.json`:
  - gộp các lần ghi tiến độ, chỉ ghi khi đổi trạng thái hoặc mỗi 5 giây;
  - tự dọn các task đã xong quá 7 ngày, hoặc giữ tối đa 300 task (`store.py:88-98, 349-367`).

**Tiếng:**
- Không bao giờ trộn tiếng gốc của clip AI (Muse). `keep_source_audio` chỉ áp dụng cho clip base hoặc clip người dùng tự đưa vào (`render.py:175`).
- Riêng nhánh Series, tiếng Muse sẽ được dùng chủ động theo GĐ 6.

### GĐ 3. Cửa sổ desktop riêng, không dùng tab Chrome
- Dùng `pywebview`, lõi là Edge WebView2 đã có sẵn trên máy. Thêm vào `pyproject.toml` và `services/requirements-agent.txt`.
- Module mới `services/agent_core/desktop.py`, chạy thành tiến trình riêng vì pystray và pywebview đều cần main thread.
  - Mở một cửa sổ app khoảng 1280×860 trỏ vào `http://127.0.0.1:<port>`.
  - Nếu cửa sổ đang mở thì mở lại sẽ chỉ focus.
  - Đóng cửa sổ thì app vẫn chạy dưới tray.
- `tray.py:123-132, 205, 240-242`: thay `webbrowser.open` bằng việc mở hoặc focus cửa sổ desktop. Nếu pywebview lỗi thì quay về `webbrowser.open`.
- `scripts/setup.ps1`: cài thêm pywebview. Kiểm tra WebView2 Runtime, thiếu thì báo.
- Ghi chú: WebView2 vẫn là Chromium, nên phần nhẹ đi chủ yếu nhờ GĐ 2. Cửa sổ riêng giúp tách khỏi Chrome, không ăn theo extension và tab khác.

### GĐ 4. Timeline tiến độ, % và thời gian còn lại
- Mô hình bước theo từng nhánh, `services/agent_core/progress.py` (mới):
  - Kịch bản → Storyboard và kiểm tra → Ảnh nhân vật (nếu có) → Tạo clip Muse (n/N, có % từng clip) → Giọng → Dựng → QA (và FIX nếu có) → Chờ duyệt.
  - Mỗi bước có trọng số.
  - Thời gian dự kiến lấy trung vị từ `timings.json` của 20 job gần nhất **cùng nhánh**. Chưa có dữ liệu thì dùng số mặc định từ `speed-plan.md`.
- Thêm mốc `script_start` còn thiếu. Ghi mốc cho giọng, QA và FIX trong `workflow._mark` (`workflow.py:998-1022`).
- `gen_tasks`: thêm cột `progress` (migration bổ sung, không phá dữ liệu cũ) và lưu `GenStatus.progress` mà hiện app đang bỏ (`engine.py:548-551`).
- UI:
  - trang job có thanh timeline: bước đã xong / đang chạy / còn lại, % tổng, đã chạy bao lâu, ước tính còn bao lâu, từng clip Muse kèm %;
  - danh sách job và dashboard có cột % và thời gian còn lại;
  - tất cả qua `/api/jobs/{id}/progress` của GĐ 2.

### GĐ 5. Tách prompt storyboard theo nhánh
- Package mới `services/agent_core/prompts/`:
  - `common.py`: chỉ phần thật sự chung, gồm khung JSON schema cảnh, luật an toàn và không bịa, ràng buộc kỹ thuật (thời lượng cảnh, `visual: generate`, tham chiếu ảnh), `HOOK_FORMULAS`.
  - `review.py`: storyboard, prompt sửa, luật gửi Muse cho Review.
    - Gồm: xưng "mình", product truth (sản phẩm là gì, cho ai, 2–4 tính năng, mỗi tính năng một cảnh bằng chứng), `missing_info`, CTA giỏ hàng, nhịp UGC (hook ≤ 1,5 s → nỗi đau → thử → bằng chứng → kết quả → CTA).
  - `viral.py`: Viral. Câu chuyện gốc, không bắt buộc sản phẩm, công thức China trend (`formula`) nằm ở đây.
  - `series.py`: Series.
    - Đọc tone, phong cách, bối cảnh từ **template** (không cứng là Suri hay "hài gia đình").
    - Hai chế độ dialogue và narration, mỗi chế độ một khối luật riêng.
    - Lịch sử các tập trước, kịch bản chủ kênh muốn, không giới hạn số cảnh nói.
  - Mỗi nhánh có `plan_prompt`, `fix_prompt`, `issues()` (luật kiểm tra riêng, ví dụ cap 2 cảnh nói chỉ áp dụng cho review/viral) và `shot_rules` cho `compile_shot`.
- `creative.branch_plan_prompt` và `branch_review_prompt` chỉ còn là bộ điều phối theo `branch`. `storyboard_issues` gọi `issues()` của đúng nhánh.
- Luật UGC mục 3 đưa vào nhánh phù hợp:
  - "nhiệm vụ hình ảnh" (proof / mechanism / consequence / action) cho mọi cảnh, cảnh không có nhiệm vụ thì bỏ;
  - "một hành động, một chuyển động máy" mỗi cảnh;
  - danh sách đen "AI slop";
  - product truth chỉ cho Review.
- Sửa các mâu thuẫn đã nêu ở Context:
  - một định nghĩa hội thoại duy nhất (`creation_branches.is_dialogue`, `_cast_plan_voices` dùng lại nó);
  - `DURATION_TARGETS` chỉ còn một nguồn;
  - bỏ lời dặn máy quay cũ và chữ "photorealistic" sai.
- Muse:
  - đánh số attachment **sau khi** đã chèn khung nối tiếp và ảnh nhân vật do app tạo;
  - cảnh không có sản phẩm thì không gửi ảnh sản phẩm.
- FIX: đưa lỗi QA của cảnh vào prompt tạo lại (`engine.regenerate_scenes`). Xử lý cả cảnh `reuse_of`.
- Xoá code chết: `branch_ideation_prompt`, prompt cũ `engine.py:147-169`, các nhánh `if branch` không bao giờ chạy ở `workflow.py:907-949`, lời gọi `creative.ideation_prompt` không tồn tại (`workflow.py:508`).
- Test hợp đồng `tests/test_branch_prompts.py`:
  - prompt Review không chứa luật Series/Suri/dialogue;
  - Series không chứa "mình"/`missing_info`/cap 2 cảnh nói;
  - Viral không chứa luật review;
  - mỗi nhánh vẫn yêu cầu đủ trường của schema.

**UGC mục 1, 2, 4** (thuộc GĐ 5 vì phụ thuộc nhánh):
- Mục 1, lớp chữ và sticker (`editor/subtitles.py`, `editor/render.py:380-386`, chèn trước `[vchain]split`):
  - chữ hook ở khung đầu cho cả 3 nhánh;
  - sticker CTA "giỏ hàng" **chỉ cho Review**;
  - nhãn nhỏ "nội dung AI" ở góc.
  - Kèm theo: sửa QA để không trừ điểm style mới (`qa_video.py:156-161, 220-223`); cho `quality.py:459-461` nhận ra HookLite và CTA; sửa câu cấm chữ trên màn hình trong prompt (`creative.py:315`).
- Mục 2, checklist QA 7 điểm cho từng clip Muse:
  - chạy bằng AI vision khi clip vừa về (`engine._check`), dùng `qa_video` hiện có;
  - clip trượt thì tạo lại, kèm lỗi cụ thể trong prompt, trong giới hạn `MAX_ATTEMPTS`.
- Mục 4, nhịp cắt ≤ 3 giây và chỉ số "cảnh tĩnh dài nhất" trong QA (`quality.inspect_rendered_video`), ngưỡng theo nhánh.

### GĐ 6. Series: Muse nói đúng kịch bản, đúng giọng, đồng nhất qua các tập

**6a. Công cụ đo** (chạy bằng CPU, nạp khi cần, tự tắt khi rảnh như VieNeu):
- **Khớp lời:** `faster-whisper` dùng model tiếng Việt (PhoWhisper-small bản CTranslate2, hoặc whisper-small nếu bản đó không có) để chép lại lời Muse nói, rồi tính WER so với câu kịch bản (bỏ dấu câu, chuẩn hoá chữ thường).
- **Giống giọng:** speaker embedding bằng ONNX (WeSpeaker ResNet34, khoảng 25 MB, chạy trên onnxruntime đã có sẵn), tính cosine với mẫu giọng của nhân vật.
- **Giống mặt và trang phục:** AI vision chấm cặp khung hình so với ảnh tham chiếu.
- Module mới `services/agent_core/editor/speech_check.py`.

**6b. Thí nghiệm thật trên Muse** (Muse không giới hạn theo memory):
- Template Suri, 4 câu thoại ngắn, mỗi biến thể 4 clip, chạy song song.
- Các biến thể, lần lượt từ đơn giản tới mạnh hơn:
  - **V1, prompt sạch:**
    - mỗi clip chỉ **một** câu, tối đa 12 từ, tiếng Việt có dấu, đặt trong ngoặc kép ở **đầu** prompt;
    - dặn rõ "nói đúng từng chữ, chỉ một lần, không nói gì khác";
    - mô tả giọng cố định cho từng nhân vật;
    - **bỏ câu bọc tiếng Trung "bỏ qua lịch sử"** của gateway cho các job này.
  - **V2, V1 cộng audio mẫu giọng của nhân vật:** gửi một file âm thanh mẫu (`audios[]`, gateway đã nhận được) kèm lời dặn "dùng đúng giọng này". Mẫu giọng ban đầu lấy từ take Muse tốt nhất của nhân vật đó; nếu chưa có thì dùng giọng mẫu VieNeu chỉ để làm mẫu.
  - **V3, V2 cộng audio câu thoại làm "track dẫn":** gửi audio đọc đúng câu đó và yêu cầu nhân vật nói theo, khớp môi. Track dẫn tạo bằng Edge TTS hoặc VieNeu, chỉ làm mẫu cho Muse, **không** dùng làm giọng cuối.
  - **V4, cùng một thread:**
    - lượt 1 giới thiệu nhân vật, ảnh và giọng;
    - các lượt sau tạo từng clip trong **cùng chat**;
    - so với thread mới mỗi clip.
- Đo mỗi biến thể: tỉ lệ câu khớp (WER ≤ 15%), độ giống giọng giữa các clip và với mẫu, điểm đồng nhất mặt và trang phục, thời gian tạo.
- Gửi chủ kênh clip mẫu và bảng số liệu. Lưu báo cáo vào `docs/reports/2026-11-16-series-muse-voice.md`.

**6c. Đưa cách tốt nhất vào nhánh Series:**
- Gateway (`services/muse_gateway/app.py`, `engine.py`):
  - thêm `thread_url`, `account_id`, `keep_history` vào `VideoRequest`;
  - mở lại thread cũ, không đổi tài khoản, không đóng context giữa các lượt;
  - mỗi thread chỉ chạy một việc tại một thời điểm;
  - trả về `thread_url`.
  - Phần này chỉ làm nếu V4 thắng.
- App:
  - `providers.submit_video` gửi audio và thread;
  - template Series lưu "bộ nhận diện" gồm ảnh tham chiếu, mẫu giọng từng nhân vật, `thread_url` theo series và tài khoản cố định (`creative_series.py`).
  - Tập 1 tạo bộ nhận diện; các tập sau dùng lại.
- **Vòng kiểm tra từng câu:**
  - clip về → chép lời và so giọng;
  - lệch kịch bản hoặc lệch giọng → tạo lại, dặn kèm chỗ sai, tối đa 3 lần;
  - chọn take có WER thấp nhất và giọng giống nhất.
- Khâu dựng dùng **tiếng Muse** của take đạt cho Series: không thay bằng VieNeu, không trộn tiếng hai nguồn.
- Lưới an toàn cuối: chỉ khi 3 lần đều trượt mới dùng VieNeu cho riêng câu đó, và job bị đánh dấu rõ. Có công tắc trong Cài đặt để tắt hẳn VieNeu cho Series.
- Cập nhật `creation_branches.py:65-70` (`VOICE_MODES`), `muse_prompt.py:100-105` và `voice_track.py` theo cách mới.

### GĐ 7. Chuẩn hoá và push
- Chạy `ruff`, toàn bộ `pytest`, sửa hết lỗi.
- Cập nhật `docs/creation-branches.md` (yêu cầu riêng từng nhánh), `docs/muse-video-engine.md` (tiếng Series, thread), `STATUS.md` (bảng trạng thái mới, ghi rõ "đã test thật" hay "chưa"), `README.md` (mở app bằng cửa sổ riêng).
- Đo lại tài nguyên bằng script GĐ 1, lưu `bench-2026-11-16-resources-after.json`, ghi bảng so sánh vào STATUS.
- Push các commit lên `origin/main`. Đây là nhánh mà nút cập nhật của app đang theo dõi.

---

## File chính sẽ sửa
- Prompt/nhánh: `services/agent_core/creative.py`, `prompts/` (mới), `creation_branches.py`, `muse_prompt.py`, `storyboard_support.py`, `workflow.py`, `quality.py`, `qa_video.py`.
- Muse: `videogen/engine.py`, `videogen/providers.py`, `muse_gateway/app.py`, `muse_gateway/engine.py`, `muse_gateway/store.py`, `creative_series.py`, `editor/voice_track.py`, `editor/speech_check.py` (mới).
- Hiệu năng/UI: `web/app.py`, `web/static/app.js`, `web/static/muse_profiles.js`, `templates/*.html`, `worker.py`, `db.py`, `editor/tts.py`, `editor/ffmpeg.py`, `editor/render.py`, `editor/subtitles.py`.
- Desktop/tiến độ: `desktop.py` (mới), `tray.py`, `progress.py` (mới), `pyproject.toml`, `services/requirements-agent.txt`, `scripts/setup.ps1`.

## Kiểm chứng
- **Test tự động:** `.venv\Scripts\python -m pytest -q` và `ruff check services tests` đều phải qua. Test mới:
  - prompt từng nhánh (không lẫn luật);
  - poll dừng khi tab ẩn (kiểm tra JS bằng trình duyệt);
  - `/api/jobs/{id}/progress` và ETA;
  - worker được đánh thức khi có việc;
  - gateway mở lại thread (fake gateway);
  - vòng kiểm tra lời và giọng (audio mẫu cố định);
  - không trộn tiếng Muse ở clip AI (trừ Series đã đạt).
- **Chạy app thật:**
  - mở bằng tray → hiện cửa sổ desktop, không mở tab Chrome;
  - tạo một job mỗi nhánh → timeline hiện %, ETA, % từng clip;
  - so RAM/CPU trước và sau bằng script đo.
- **Muse thật:** thí nghiệm 6b có số liệu. Sau đó chạy một tập Series hoàn chỉnh (2 tập liên tiếp) bằng cách thắng cuộc. Gửi chủ kênh clip để nghe và xem.
- **Nói thẳng nếu không đạt:** nếu không biến thể nào làm Muse nói đúng kịch bản, báo cáo sẽ ghi rõ số liệu và giới hạn. Series khi đó vẫn chạy với vòng thử lại và lưới an toàn, không âm thầm che lỗi.
