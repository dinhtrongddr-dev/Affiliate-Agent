# Series: Muse nói tiếng Việt đúng kịch bản (đo thật, 16/11/2026)

**Câu hỏi của chủ kênh:** Muse có thể tự nói tiếng Việt đúng kịch bản, đúng giọng nhân vật mà không cần VieNeu không? Tạo trong cùng một đoạn chat có tự đồng nhất không?

**Cách đo:**
- Template Suri, 4 câu thoại ngắn: Suri, mẹ, bố, rồi Suri lần nữa.
- 4 cách nhắc, mỗi cách 4 clip, tổng 16 clip Muse thật qua gateway tích hợp.
- Lời nói: chép bằng PhoWhisper-small rồi so với câu kịch bản (WER = tỉ lệ từ sai).
- Giọng: WeSpeaker ResNet34.
- Giống ảnh nhân vật: AI vision chấm 0–10.
- Số liệu gốc: `docs/reports/2026-11-16-series-muse-voice.json`. Clip nằm ở `E:\AffiliateAgentMedia\experiments\series-voice-20261116-2127\`.

## Kết quả

| Cách nhắc | Câu nói đúng (WER ≤ 15%) | WER TB | Giọng Suri giống nhau giữa 2 clip (cosine) | Giống mẫu giọng VieNeu | Giống ảnh nhân vật | Thời gian TB/clip |
|---|---|---|---|---|---|---|
| **V1** câu thoại đặt đầu prompt, "nói đúng từng chữ, một lần", mô tả giọng cố định, bỏ câu bọc tiếng Trung | **4/4** | 0,11 | **0,735** | 0,28 | **8,9** | 11 phút |
| V2 = V1 + file mẫu giọng của nhân vật | 4/4 | **0,025** | 0,669 | 0,26 | 8,1 | 11 phút |
| V3 = V2 + file đọc đúng câu đó làm "track dẫn" | 4/4 | **0,025** | 0,689 | 0,32 | 8,6 | 20 phút |
| V4 = V1 trong **cùng một chat**, ảnh nhân vật chỉ gửi ở lượt đầu | 2/4 | 0,39 | 0,728 | 0,17 | 4,7 | — |

**So với lần thử 15/11 (0/8 câu đúng):** cả 12 clip của V1–V3 đều nói đúng câu thoại. Từ sai thường chỉ là mất chữ đầu ("mẹ") hoặc lặp một chữ ("sao sao"). Muse hay chêm 1–2 tiếng ở cuối ("thôi", "he he").

Nguyên nhân chính khiến lần trước sai:
- prompt cũ bị câu bọc tiếng Trung "严禁使用历史…" bao ngoài;
- một cảnh có tới 2 câu của 2 người;
- câu thoại nằm giữa prompt, không đặt ở đầu.

## Kết luận

1. **Muse nói được tiếng Việt đúng kịch bản** khi mỗi cảnh chỉ có MỘT câu ngắn (≤ 12 từ) của MỘT người, câu đặt ở đầu prompt, dặn "nói đúng từng chữ, một lần". App chọn **V1** làm mặc định vì:
   - đơn giản nhất, không cần file âm thanh;
   - giữ ảnh nhân vật tốt nhất;
   - giọng giữa các clip giống nhau nhất.

   V2/V3 có WER thấp hơn một chút, nhưng mẫu 4 clip là quá nhỏ để chắc chắn. V3 còn chậm gấp đôi.
2. **Gửi file mẫu giọng KHÔNG làm Muse nói bằng giọng đó.** Độ giống với mẫu VieNeu chỉ 0,26–0,32 ở mọi cách. Muse tự chọn giọng theo mô tả.
   - Giọng của cùng một nhân vật giữa các clip giống nhau khoảng 0,67–0,74. Mức này tương tự cùng một người nói ở hai lần thu khác nhau, nhưng chưa phải giống hệt.
   - Để giữ giọng qua các tập, app dùng **cùng một câu mô tả giọng** cho mỗi nhân vật ở mọi clip và mọi tập (`muse_prompt.voice_description`).
3. **"Cùng một đoạn chat thì tự đồng nhất": chưa chứng minh được, và hiện không dùng.**
   - Lượt 1 chạy tốt. Ở các lượt sau, gateway lấy nhầm video cũ trong chat làm kết quả: clip "xong" sau 40 giây, trùng hệt clip lượt 1. Bản vá thứ nhất (chỉ nhận video xuất hiện sau các video cũ) chưa đủ.
   - Lượt 4 tạo được clip mới, nói đúng câu, nhưng không gửi lại ảnh thì AI chấm không nhận ra nhân vật.
   - Vì vậy app vẫn gửi ảnh nhân vật cho mọi clip. Gateway giữ khả năng `thread_url` để thử lại sau, nhưng app không bật.
4. **Thời gian:** hôm nay Muse chậm, 2–25 phút một clip; gateway tích hợp làm từng clip một.

## Đã đưa vào app

Áp dụng cho nhánh Series hội thoại, công tắc `AA_SERIES_NATIVE_VOICE`, bật mặc định:
- **Prompt:** prompt từng cảnh theo kiểu V1, câu thoại đặt đầu tiên. Prompt Series chỉ cho mỗi nhịp một câu ≤ 12 từ của một người.
- **Kiểm tra từng take:**
  - chép lời rồi so với câu kịch bản;
  - sai quá 30% số từ, hoặc thừa quá 4 từ, thì tạo lại, kèm dặn "phải nói đúng: …" và câu Muse đã nói sai;
  - tối đa 3 lần.
- **Dựng:** take đạt giữ **giọng Muse** ở âm lượng đầy đủ; phụ đề theo đúng đoạn Muse nói; không lồng VieNeu.
- **Dự phòng:** chỉ câu nào sai cả 3 lần mới lồng VieNeu (`AA_SERIES_VIENEU_FALLBACK`, có thể tắt).

## Còn lại

- Chưa dựng thử trọn một tập Series 2 tập liên tiếp bằng luồng mới. Đó là bước kiểm chứng tiếp theo.
- Độ giống giọng giữa các tập mới đo trong một lượt. Cần đo lại khi có 2–3 tập.
- Tính năng chat chung: muốn thử tiếp phải sửa cách gateway nhận diện video mới trong chat cũ (ví dụ đối chiếu theo mã tin nhắn thay vì vị trí).
