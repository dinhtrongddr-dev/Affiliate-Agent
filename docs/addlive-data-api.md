# Dữ liệu Sàn Cam — AddLiveTag

Mở **Sản phẩm Shopee** (`/catalog`) để xem dữ liệu và chọn sản phẩm làm video.
Đường dẫn cũ `/intelligence` chuyển về cùng màn hình. Xem [lịch tự động](shopee-automation.md)
để biết cách đồng bộ hằng ngày khi app đóng; các API tra cứu bên dưới vẫn được giữ.

## Kết nối

- Nguồn cố định: `https://data.addlivetag.com`.
- Tài liệu hiện tại yêu cầu API key từ 01/10/2026: [API key](https://github.com/bcat95/shopee-aff/blob/main/docs/api-key.md).
- Lưu key tại trang dữ liệu. Máy chủ gửi `X-API-Key`; key không nằm trong JavaScript, URL, HTML hoặc `.env`.
- Cấu hình nằm trong `state/secrets/addlive_api.bin`, mã hoá Windows DPAPI cho người dùng hiện tại. Ô key trống giữ giá trị đã lưu.
- Tier trong giao diện luôn là **phần trăm**: nhập `0.5` nghĩa là **0,5%**, nhập `8` nghĩa là **8%**. Trần tính bằng VND. Để trống tier nếu chưa biết; không coi hoa hồng của tài khoản nguồn là mức đã xác nhận cho tài khoản của bạn.
- Affiliate ID là mã publisher riêng, không phải API key. Chỉ khi đã nhập ID này, app mới tạo link `an_redir`; Custom Link hiện có được giữ lại.

## Dữ liệu đã nối

| Tác vụ | Endpoint |
|---|---|
| Một ID/link gốc hoặc một link rút gọn | `/product-data/product-data.php` |
| Lô tối đa 100 ID/link gốc | `/product-data/product-data-batch.php` |
| Giá và hoa hồng theo ngày, tối đa 50 ID | `/price-tracking/history.php` |
| Sản phẩm có hoa hồng | `/offers/product-offer.php` |
| Shop / thương hiệu | `/offers/shop-offer.php` |
| Chiến dịch | `/offers/shopee-offer.php` |
| Sản phẩm của một shop | `/offers/shop-products.php` |

Đồng bộ hằng ngày dùng một lô có giới hạn ngân sách nguồn; thêm tay dùng một lượt
tra sản phẩm. API tra lô vẫn hỗ trợ `cache_only=true` và `max_api=0`. Không tự chia
lô, quét hết trang hoặc tự thử lại trong ngày. Khi HTTP 429 hoặc cờ nguồn yêu cầu
nghỉ, client giữ thời gian chờ qua các request trong cùng tiến trình. HTTP 401/403
dừng lượt tra và hiển thị lỗi, không thử key sang nguồn khác.

## Diễn giải và lưu dữ liệu

- `api`: có thể ghi một mốc quan sát mới. `db`: cập nhật thông tin hiện tại nhưng không tạo mốc bán mới từ thời gian đọc cache.
- `stale`, `skipped`, `not_found`, `error`, `fallback`, cảnh báo nguồn: giữ kết quả để kiểm tra, không coi là dữ liệu mới hoặc ghi đè kho bằng kết quả đó.
- Giá, số lượng bán, phần trăm Shopee/Xtra và số tiền hoa hồng là các trường riêng. Giá trị 0 được giữ; trường thiếu không được biến thành số 0 để thay dữ liệu đã có.
- Provenance của lượt nhập nằm trong bảng `addlive_import_evidence` của SQLite hiện tại. Không chứa API key.
- Lịch sử giữ nguyên `recorded`, `suspect`, cảnh báo tier và giới hạn cắt dữ liệu. Lịch sử giá không được dùng làm bằng chứng tăng trưởng bán.
- Link affiliate, trạng thái video và quyết định quyền media của chủ sở hữu được giữ. Ảnh từ API không tự được coi là có quyền tái sử dụng.

Tài liệu nguồn nêu phạm vi miễn phí cho học tập, nghiên cứu và công cụ nội bộ, không dùng thương mại. Việc dùng dữ liệu phải theo phạm vi cho phép của nhà cung cấp.

## Kiểm tra

Các test client/service/web dùng `httpx.MockTransport`, API key giả và database tạm. Kiểm tra LIVE phải được báo cáo riêng; test giả không xác nhận key hoặc nguồn đang hoạt động. Một lần đọc cache có xác thực là đủ để kiểm tra kết nối ban đầu, không cần tạo video hay gọi Muse.
