# Plan: giao diện nhanh như API, icon thống nhất, dọn mã frontend

## Context

Chủ kênh thấy mở các tab trong app chậm, trong khi gọi API thì nhanh. Yêu cầu: rà soát icon, audit app, tối ưu mã và tốc độ hiển thị, tìm điểm bất cập của giao diện rồi lên plan. **Bước này không sửa code.**

### Đo thật trên dữ liệu hiện có (17/11)

Thời gian server trả byte đầu tiên:

| Trang | Thời gian | Dung lượng HTML |
|---|---|---|
| /trends | **57–66 s** | 713 KB |
| /china | **17–21 s** | 966 KB |
| /generation (Video AI) | **10–14 s** | 155 KB |
| /tasks (Hàng đợi) | **10 s** | 40 KB |
| /catalog | 3,4–5,6 s lần đầu, 0,3 s sau đó | 1,08 MB |
| /jobs | 1,5–1,9 s | 17 KB |
| / | 0,75–0,9 s | 11 KB |
| /api/status | 4 ms | — |

### Nguyên nhân chính (đã xác nhận trong code)

Bộ lọc giờ `localtime` (`localtime.py:11-15`) gọi `get_settings()` cho **từng dòng thời gian**. Mà `get_settings()` (`settings.py:186`) tạo `Settings()` mới mỗi lần, tức đọc lại `.env` và khoảng 100 trường, mất khoảng 70–85 ms.

Số lần gọi khớp với thời gian đo được:

| Trang | Số dòng thời gian | Ước tính | Đo được |
|---|---|---|---|
| /trends | 919 | ~60 s | 57–66 s |
| /china | ~300 | ~20 s | 17–21 s |
| /tasks | 200 | ~13 s | 10 s |
| /generation | ~203 | ~13 s | 10–14 s |
| /jobs | 27 | ~1,8 s | 1,5–1,9 s |

### Nguyên nhân phụ theo trang

- **/trends:**
  - N+1 truy vấn: mỗi chủ đề gọi `matches_for`, `get_product` ×5 và `observations`, tổng khoảng 500 kết nối SQLite (`web/app.py:326-330`).
  - `catalog.ensure_schema` chạy `executescript` ở mỗi lần đọc sản phẩm (`catalog.py:68-74`).
  - Hiển thị cùng lúc cả 202 chủ đề kèm bảng chi tiết ẩn.
- **/china:**
  - Đọc 2.000 chủ đề chỉ để tra tên (`web/china.py:31`).
  - Thống kê ngách đọc thêm 1.000 dòng (`cn_trends/analysis.py:305`).
  - Hiển thị cả 4 tab cùng lúc.
  - Lỗi so chuỗi ngày làm bộ lọc 96 giờ không có tác dụng (`cn_trends/store.py:183`).
- **/catalog:**
  - Mỗi lần mở trang đều chạy `reconcile` và **lấy khoá ghi DB** (`app.py:609`).
  - Gọi PowerShell đọc Task Scheduler mất 3–5 s, cache chỉ 60 s (`shopee_schedule.py:58-85`).
  - Giải mã DPAPI 2 lần.
  - Mỗi dòng có sẵn một form sửa 14 ô, khoảng 3,5 KB/dòng; không giới hạn số dòng.
- **/generation:**
  - Gọi HTTP gateway (`healthy` + `/admin/accounts`, timeout tới 6,5 s) ở mỗi lần mở trang.
  - Giải mã phiên Muse không cache.
  - Liệt kê 300 task.
- **Cả app:**
  - Mỗi lần chuyển tab sidebar là tải lại cả trang.
  - `app.js` nằm trong `<head>`, chặn hiển thị.
  - Trang tự tải lại khi trạng thái đổi, có thể xoá chữ đang gõ.

### Icon và giao diện (audit)

- **Icon:**
  - Toàn bộ là emoji. 🇨🇳 hiện thành chữ "CN" trên Windows 10.
  - Emoji màu lẫn emoji đen trắng (🎞 ⏱ ⬆ ▶ ✦ thiếu ký tự màu U+FE0F).
  - Một icon cho nhiều nghĩa: 🎬 = Video / Kịch bản / Làm video / Tạo lại; ⬆ = tải lên / cập nhật; sản phẩm lúc 📦 lúc 🛒.
  - 3 kiểu "làm mới" (🔄 ⟳ ↻), 3 kiểu "xoá"; 💾 và ↗ dùng không đều.
- **Hành vi:**
  - 65 chỗ tải lại cả trang sau thao tác, có chỗ chờ cứng 6–8 s.
  - Hơn 20 nút gọi API không khoá khi bấm, bấm đúp là gửi 2 lần.
  - 67 đoạn JS viết thẳng trong `onclick`.
- **Mã frontend:**
  - Các hàm trùng lặp: nghe thử giọng ×3, kéo-thả tải lên ×3, helper DOM ×3.
  - `intelligence.html` (22 KB) là file chết.
  - JS inline lớn: `create.html` 11,7 KB, `job.html` 9 KB.
  - 15 ảnh mẫu phong cách 983 KB.
- **Hoàn thiện giao diện:**
  - 12/15 trang có tab trình duyệt cùng tên "Affiliate Agent".
  - Ảnh thiếu `alt`; nút chỉ có icon mà không có nhãn.
  - Badge cảnh báo/OK có độ tương phản thấp (2,5–3:1).
  - Sidebar đè chân trang khi màn hình thấp; tab và bảng tràn khi cửa sổ hẹp.
  - Hàm `tabs()` ghi đè `?tab=` trên URL.
- **Bảo mật nhỏ:** `POST /jobs/{id}/affiliate` nằm ngoài `/api/` nên không qua lớp chặn CSRF.

**Mục tiêu:** mọi trang trả về **< 300 ms** trên dữ liệu hiện có; HTML trang lớn **< 200 KB**; một bộ icon thống nhất; không tải lại cả trang cho thao tác thường.

---

## Thứ tự thực hiện

Mỗi giai đoạn có commit riêng; test và ruff phải qua; đo lại bằng script ở GĐ A.

### GĐ A. Thước đo trước khi sửa
- Script `scripts/bench_pages.py`: đo TTFB và dung lượng HTML từng trang 3 lần, lấy trung vị, ghi JSON vào `docs/reports/`.
- Thêm header `Server-Timing` (thời gian render) qua middleware trong `web/app.py` để thấy ngay trên trình duyệt.
- Lưu số liệu "trước" vào `docs/reports/bench-2026-11-17-pages-before.json`.

### GĐ B. Sửa nguyên nhân chính (một chỗ, dự kiến /trends từ 60 s xuống khoảng 2 s)
- `settings.py`: `get_settings()` dùng cache trong tiến trình, thêm `reset_settings_cache()`. Gọi reset ở mọi chỗ lưu cài đặt:
  - `web/app.py:1139`, `:1164`;
  - `env_file.update_env`, để cách nào ghi `.env` cũng xoá cache.
- `localtime.zone()`: nhớ múi giờ đã tính; reset cùng cache settings.
- Test: gọi `fmt` 1.000 lần dưới 50 ms; lưu cài đặt thì múi giờ mới có hiệu lực.

### GĐ C. Từng trang chậm
- **/trends** (`web/app.py:311-339`, `trend_intel/store.py`, `catalog.py`):
  - Gom matches, sản phẩm và observations thành 3 truy vấn `IN (...)`.
  - Cache `catalog.ensure_schema` theo kiểu `_ready` như `trend_intel/store.py:77`.
  - Phân trang 50 chủ đề.
  - Bảng điểm và observation tải khi mở `<details>`, qua API mới `GET /api/trends/{key}/details`.
- **/china** (`web/china.py:19-41`, `cn_trends/store.py`, `cn_trends/analysis.py`):
  - Tra tên chỉ cho các chủ đề có ý tưởng (`WHERE key IN`).
  - Thống kê ngách bằng `GROUP BY`.
  - Sửa so ngày (dùng cùng định dạng ISO).
  - Tab không đang mở tải qua API khi bấm; mỗi tab phân trang 50 dòng.
- **/catalog** (`web/app.py:604-615`, `templates/catalog.html`):
  - Bỏ `reconcile` khỏi GET; giữ ở bước import/sync như `app.py:635`.
  - Trạng thái lịch Task Scheduler đọc nền, tải sau qua API; tăng thời gian cache.
  - Một form sửa dùng chung, JS điền dữ liệu dòng được chọn.
  - Phân trang 50 dòng, có ô tìm kiếm.
- **/generation** (`web/app.py:341-379`, `videogen/muse_session.py`):
  - Khối trạng thái gateway/tài khoản tải sau qua `/api/muse/profiles` và `/api/muse/gateway` (cache 30 s), không chặn render.
  - `muse_session.load` cache theo mtime file.
  - Task giới hạn 50, có phân trang.
- **/jobs, /tasks, /analytics:** phân trang 50; `conversion_summary` có LIMIT.

### GĐ D. Chuyển trang mượt hơn
- `base.html`: đưa `<script src=app.js>` xuống cuối `<body>`, trước `{% block scripts %}`.
- Tải trước khi rê chuột vào link sidebar: `<link rel=prefetch>` qua JS khi hover, chỉ với trang < 300 ms.
- Trạng thái tự cập nhật (`app.js` pollJob/watchJobRows/watchTasks): khi người dùng đang gõ hoặc có ô đã sửa thì hiện dải "Có cập nhật – bấm để tải lại", không tự tải lại.

### GĐ E. Bộ icon thống nhất
- Một sprite SVG nội tuyến, khoảng 25 icon chọn từ **Lucide** (giấy phép ISC, ~4 KB), trong `templates/_icons.html`.
  - Macro `icon('name')`, kích thước cố định, cùng màu chữ.
  - Hiển thị giống nhau trên mọi máy Windows.
- **Một khái niệm, một icon:**
  - tổng quan = home; sáng tạo = sparkles; video = film; trend = flame; trend TQ = globe; sản phẩm = package (dùng mọi nơi);
  - clip AI = clapperboard; doanh thu = chart; bài học = lightbulb; hàng đợi = list; cài đặt = settings; nhật ký = file-text;
  - tải lên = upload; cập nhật = refresh-cw; làm mới = rotate-cw; xoá = trash; lưu = save; mở ngoài = external-link; làm video = play.
- Thay emoji ở sidebar, nút, tab (`base.html`, `job.html`, `create.html`, `catalog.html`, `trends.html`, `china.html`, `dashboard.html`, `app.js`).
- Đổi tên "Video AI" thành "Clip AI (Muse)" để khỏi lẫn với "Video".
- Thêm chấm màu cho trạng thái worker: sẵn sàng / đang chạy / tắt / mất kết nối.
- Mọi link `target=_blank` tự có icon mở ngoài bằng CSS.

### GĐ F. Hành vi và trải nghiệm
- **`api()` trong `app.js`:**
  - nhận `btn`, tự khoá nút và hiện vòng quay trong lúc gửi;
  - một bộ lắng nghe chung cho `data-action` / `data-url` / `data-confirm` thay cho JS trong `onclick`.
  - Chuyển dần các nút ở `job.html`, `trends.html`, `china.html`, `catalog.html`, `settings.html`, `generation.html`, `dashboard.html`.
- **Bớt tải lại cả trang:**
  - lưu caption / số liệu / xoá dòng / bỏ qua / duyệt ngách thì cập nhật ngay tại chỗ kèm toast;
  - thao tác dài (thu thập trend, chấm lại) poll trạng thái task thay vì chờ cứng 6–8 s.
- **Hoàn thiện giao diện:**
  - `{% block title %}` cho mọi trang;
  - trạng thái rỗng cho bảng ngách và KPI;
  - sửa `create.html:297` khi chưa có series;
  - `alt` cho ảnh, `aria-label` cho nút chỉ có icon, `role=tab`/`aria-selected` cho tab;
  - nối nhãn với ô nhập.
- **Màu sắc:** tăng tương phản badge (`#9a5b00`, `#0b7a3b`, `#b42318`, `#2b56b0`) và nút chính (`#d03a1b`) trong `app.css`.
- **Bố cục:**
  - sidebar là cột flex có `overflow-y:auto`, chân trang `margin-top:auto`;
  - dưới 1000 px dùng thanh điều hướng ngang cuộn được;
  - `.tabs` và bảng có cuộn ngang.
- **`tabs()`:** `?tab=` hoặc `#hash` thắng lựa chọn đã lưu; khoá lưu theo loại trang, không theo từng job.

### GĐ G. Dọn mã
- Xoá `templates/intelligence.html`, vì `/intelligence` đã chuyển hướng sang `/catalog`.
- JS inline lớn chuyển ra file tĩnh có cache: `static/job.js`, `static/create.js`, `static/catalog.js`. Dữ liệu truyền qua một khối `<script type="application/json">`.
- Gộp hàm trùng vào `app.js`: nghe thử giọng, kéo-thả tải lên, `el()`, `busyForm`. Thay `fetch` tự viết header bằng `api()`.
- JSON debug của job (storyboard thô, brief, phân tích) tải khi mở tab Phân tích, qua `/api/jobs/{id}`, không nhúng sẵn vào HTML.
- CSS:
  - lớp tiện ích `.pre-box`, `.alert-err`, `.scroll-x`, `.mb16`;
  - chuyển style riêng của `create.html` / `catalog.html` vào `app.css`;
  - màu dùng biến CSS.
- 15 ảnh mẫu phong cách đổi sang WebP 300×400 (khoảng 20 KB mỗi ảnh); thêm ảnh nhỏ cho ô tóm tắt.
- Chuyển `POST /jobs/{id}/affiliate` thành `/api/jobs/{id}/affiliate` để qua lớp chặn CSRF.

---

## File chính
- **Tốc độ:**
  - `services/agent_core/settings.py`, `localtime.py`, `env_file.py`
  - `web/app.py`, `web/china.py`
  - `catalog.py`, `trend_intel/store.py`, `cn_trends/store.py`, `cn_trends/analysis.py`
  - `shopee_schedule.py`, `videogen/muse_session.py`, `db.py`
- **Giao diện:** `web/templates/base.html`, `_icons.html` (mới), `_macros.html`, `job.html`, `create.html`, `catalog.html`, `trends.html`, `china.html`, `generation.html`, `dashboard.html`, `settings.html`, `tasks.html`, `jobs.html`
- **Tĩnh:** `web/static/app.js`, `app.css`, `job.js`, `create.js`, `catalog.js` (mới), `static/styles/*.webp`
- **Đo:** `scripts/bench_pages.py` (mới)

## Kiểm chứng
- **Tốc độ:** `scripts/bench_pages.py` trước và sau. Mục tiêu mọi trang < 300 ms, HTML < 200 KB. Test tự động gửi request vào từng trang với dữ liệu mẫu lớn (500 trend, 300 task) và đặt ngưỡng thời gian.
- **Chức năng:** toàn bộ `pytest` và `ruff`; test mới cho cache settings, phân trang, API chi tiết trend, `tabs()` (đã có `tests/test_templates_js.py` để kiểm cú pháp JS).
- **Giao diện:** mở từng trang trong cửa sổ app và trình duyệt tích hợp, chụp màn hình sidebar/nút/tab để so icon trước và sau; thử cửa sổ hẹp 900 px và thấp 650 px; kiểm console không lỗi.
- **Máy khác:** sau khi push, cập nhật máy Admin bằng Desktop Commander, đo lại `/trends` và `/tasks` trên máy đó.
