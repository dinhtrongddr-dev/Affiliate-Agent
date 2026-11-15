# Chọn model AI (ChatGPT2API), 15/11/2026

Mục tiêu: một model duy nhất cho mọi tác vụ chữ và hình của app. Model phải nhanh hơn mà vẫn đủ thông minh.

## Cách đo

- Có 21 model chữ trên ChatGPT2API, model nào cũng trả lời được câu hỏi nhỏ (6-14 giây, phần lớn là độ trễ cổng web).
- Mức suy luận chỉ có hai chế độ: `extended` hoặc mặc định. Các giá trị low, medium, high bị phía trên trả lỗi 422.
- Đo trên 3 tác vụ thật của app, gọi qua `workflow._ask_json` y như lúc app chạy:
  - **A**: storyboard video kiến thức "Vì sao rễ cây đước mọc ngược lên khỏi bùn".
  - **B**: storyboard tập series hội thoại Suri.
  - **C**: QA hình với 6 khung hình của một tập Suri đã render.
- Chất lượng A và B: hai giám khảo `gpt-6-thinking` và `gpt-6.1-sol-wm` (cả hai dùng extended) chấm mù theo thang 1-10, trên 5 tiêu chí: hook, mạch truyện, tiếng Việt tự nhiên, mô tả cảnh, đúng đề.
- Đo 10 cấu hình ở lượt 1, rồi 5 cấu hình tốt nhất ở thêm 2 lượt.
- Mọi cấu hình đều qua hết kiểm tra bằng code: JSON hợp lệ, đúng thời lượng, đúng dạng hội thoại.

## Kết quả (trung bình 3 lượt)

| Cấu hình | Điểm | A (giây) | B (giây) | C (giây) |
|---|---|---|---|---|
| **gpt-6-thinking (mặc định)** | **6,37** | 80 | 68 | 42 |
| gpt-6.1-sol-wm + extended | 6,20 | 171 | 178 | 65 |
| gpt-6.1-sol-wm | 6,10 | 57 | 67 | 28 |
| gpt-6-astra-wm | 5,98 | 61 | 64 | 30 |
| gpt-6-instant | 5,58 | 55 | 46 | 39 |

Các cấu hình chỉ đo 1 lượt:

| Cấu hình | Điểm | A (giây) | B (giây) | C (giây) |
|---|---|---|---|---|
| gpt-6-astra-wm + extended (cấu hình cũ) | 6,65 | 284 | 211 | 53 |
| gpt-6 + extended | 6,90 | 288 | 207 | 61 |
| gpt-6 | 6,10 | 64 | 71 | 30 |
| gpt-6-mini | 5,95 | 61 | 63 | 24 |
| gpt-6.1-terra-wm | 5,90 | 64 | 68 | 39 |

## Chọn

`gpt-6-thinking`, mức suy luận mặc định (`AA_AI_THINKING_EFFORT` để trống).

- Điểm cao nhất sau 3 lượt.
- Nhanh hơn cấu hình cũ khoảng 3 lần, chủ yếu ở bước viết storyboard.
- `extended` làm mọi model chậm gấp 3-4 lần. Lợi ích về chất lượng không ổn định: sau 3 lượt, `sol-wm + extended` vẫn thấp hơn `gpt-6-thinking` ở mức mặc định.

Lệnh `setup.ps1` (cũng chạy khi `update.ps1`) chuyển các máy còn dùng mặc định cũ `gpt-6-astra-wm` sang model này. Model do chủ máy tự chọn được giữ nguyên.

## Đo lại

Chạy lại khi ChatGPT2API có model mới. Các script đo (`ai_probe.py`, `ai_bench.py`, `ai_judge.py`) chỉ nằm trong thư mục scratchpad của phiên làm việc này, không có trong repo. Muốn đo lại thì cần đưa chúng vào `scripts/` trước.
