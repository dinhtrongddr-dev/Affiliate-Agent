# Dữ liệu Shopee tự động

## Một nơi quản lý

Mở **Sản phẩm Shopee** (`/catalog`) để xem sản phẩm, giá, hoa hồng, điểm tiềm năng,
độ mới dữ liệu và chọn sản phẩm viết review. `/intelligence` và `/shopee` chuyển
về cùng màn hình. Dữ liệu cũ được nối sang danh sách chung; lịch sử quan sát,
link của chủ tài khoản, trạng thái video và quyết định quyền media được giữ lại.

Thao tác thường ngày chỉ cần dán link/ID vào **Thêm sản phẩm hấp dẫn** khi muốn
theo dõi một sản phẩm cụ thể. Nút **Ẩn sản phẩm** bỏ sản phẩm khỏi danh sách và
lượt tự đồng bộ, giữ lịch sử đã thu thập. Thêm lại bằng link/ID để theo dõi tiếp.

## Lịch khi app đóng

Mặc định đồng bộ lúc **07:00 mỗi ngày**, giờ Việt Nam. Windows Task Scheduler
chạy tác vụ `\AffiliateAgent\Shopee Daily Data Sync` bằng Python của dự án và
`scripts/sync-shopee.py`. Tác vụ chỉ lấy dữ liệu; không mở app, gọi AI/Muse,
tạo video hoặc đăng bài. App đang mở cũng kiểm tra lịch mỗi phút.

Máy phải bật, có mạng và Windows đang đăng nhập bằng tài khoản đã lưu key.
App có thể đóng hoặc Windows khóa màn hình. Lịch dùng `StartWhenAvailable`
để chạy bù giờ bỏ lỡ khi Windows có thể chạy tác vụ. Không thể cập nhật khi
máy tắt hoặc tài khoản đã đăng xuất. Đồng bộ dùng ngày theo cấu hình app;
giữ múi giờ Windows và app đồng nhất (Việt Nam: UTC+7).

Lưu kết nối hoặc lịch trong **Cài đặt nâng cao** để đăng ký/cập nhật lịch.
Màn hình chỉ xác nhận chạy khi app đóng nếu tác vụ đúng cài đặt này và đang bật.
Nếu Windows từ chối tạo tác vụ, màn hình báo lỗi; không coi là cài lịch thành công.

## Cách lấy và xếp hạng dữ liệu

- Mỗi ngày tìm tối đa 20 sản phẩm theo một từ khóa ngách; từ khóa xoay vòng
  từ **Cài đặt → Từ khoá ngách**.
- Một lô tối đa 100 ID gồm sản phẩm đang theo dõi và sản phẩm vừa tìm.
  Sản phẩm mới được xen kẽ với sản phẩm cũ cần cập nhật để có lượt lấy nguồn.
  Mặc định giới hạn 10 lượt lấy dữ liệu mới từ nguồn/ngày, có thể đặt 1–20.
  Hạn mức này dành cho tự động; thêm sản phẩm bằng tay là yêu cầu riêng.
- Dữ liệu dùng lại từ cache vẫn ghi rõ là cache. Không tạo quan sát bán mới
  từ cache, không đổi thời điểm nguồn thành thời điểm đọc cache.
- Win Score được tính khi đọc danh sách; tốc độ bán cần các quan sát thực tế
  ở các thời điểm khác nhau. Điểm thiếu bằng chứng hiển thị tạm tính.
- Mỗi ngày chỉ một phiên tự động giữa app, CLI và lịch Windows. Ngân sách
  được giữ chỗ trước khi gọi mạng. Lỗi/hết hạn giữa chừng không tự phát lại
  trong ngày; ngày sau có phiên mới. Nút Đồng bộ ngay cũng giữ quy tắc này.
- Khi nhà cung cấp báo cooldown/hạn mức, dừng gọi tiếp và hiển thị trạng thái.
  Cập nhật một phần giữ lại dữ liệu đã nhận; không báo hoàn tất toàn bộ.

## Affiliate và video

API key dữ liệu được mã hóa trong kho bí mật Windows. Trong cài đặt nâng cao,
nhập **Affiliate ID của bạn** một lần nếu muốn tạo link tự động. Key dữ liệu
không thay cho Affiliate ID. Không dùng link hoa hồng thuộc tài khoản của nhà
cung cấp; Custom Link của bạn đã lưu được giữ nguyên.

Luồng review chọn sản phẩm từ danh sách này để đưa dữ liệu vào storyboard.
Hai nhánh viral và series nhân vật vẫn dùng quy trình riêng và gợi ý sản phẩm
phù hợp sau đó. Lịch dữ liệu không tự kích hoạt các nhánh tạo video.

## Kiểm tra vận hành

Xem trạng thái/lần gần nhất/lần tiếp theo ở đầu màn hình. Nhật ký xoay vòng:
`logs/shopee-schedule.log` (khoảng 512 KB, một bản trước). Có thể chạy tác vụ
chỉ dữ liệu bằng `.venv\Scripts\python.exe scripts\sync-shopee.py` hoặc CLI
`sync-shopee`; cùng ngày đã giữ chỗ sẽ bỏ qua, không gọi lại nguồn.

Đổi đường dẫn cài đặt hoặc tài khoản Windows cần đăng ký lịch lại. Bộ cài lịch
không ghi đè tác vụ cùng tên thuộc một thư mục cài đặt khác.
