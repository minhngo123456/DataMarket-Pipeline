# Ingestion workers

Khu vực dành cho tiếp nhận dữ liệu từ các nguồn: CSV, REST API và trang HTML tĩnh được phép thu thập. Mỗi job cần trạng thái, tenant do backend gắn, source ID, retry có giới hạn và xử lý lỗi riêng.

## Các thành phần đã triển khai

1. **`dispatcher.py`**:
   - Điều phối lịch thu thập từ Scheduler (chu kỳ 6 giờ).
   - Quét các nguồn đang bật (`enabled`) theo quota của từng tenant hoạt động và tạo job vào `APIQueue` hoặc `WebQueue`.

2. **`csv_worker.py`** (Phối hợp với Hoàng):
   - Tiếp nhận sự kiện S3 Object Created qua `CSVQueue`.
   - Kiểm tra ràng buộc tenant/key/version, kích thước file (< 1 MiB) và số dòng (< 1000).
   - Đẩy các bản ghi hợp lệ vào `TransformQueue` theo lô.

3. **`api_worker.py`** (Hướng):
   - Xử lý job từ `APIQueue`.
   - Thu thập dữ liệu JSON từ REST API endpoint cấu hình trong Source.
   - Bóc tách danh sách sản phẩm, kiểm tra các trường bắt buộc (`source_sku`, `price`, `currency`, `observed_at`).
   - Lỗi 429, timeout, HTTP 408/5xx và lỗi worker ngoài dự kiến được thử lại với backoff tăng dần có giới hạn; tôn trọng `Retry-After`.
   - Lỗi xác thực, HTTP 4xx khác và payload sai được đánh dấu job `failed`; SQS chuyển lỗi retry quá số lần vào DLQ.
   - Consumer Lambda được nối với `APIQueue` trong SAM, bật partial batch response.
   - Đẩy các dòng vào `TransformQueue` cho Normalizer.

4. **`web_worker.py`** (Hướng):
   - Xử lý job từ `WebQueue`.
   - Thu thập HTML tĩnh được phép qua HTTP.
   - Bóc tách bảng dữ liệu giá hoặc thẻ sản phẩm có `data-*` attribute bằng bộ phân tích chuẩn `HTMLTablePriceParser`.
   - Nhận dạng một số tên cột SKU/giá tiếng Việt và tiếng Anh; ưu tiên giá khuyến mãi khi đang áp dụng, chuẩn hóa cách ghi giá phổ biến và dùng thời điểm job thu thập nếu nguồn không có thời điểm.
   - Kiểm tra giới hạn dung lượng, số dòng và cô lập tenant.
   - Lỗi 429, timeout, HTTP 408/5xx và lỗi worker ngoài dự kiến được retry với backoff giới hạn; lỗi xác thực, HTTP 4xx khác và HTML sai được đánh dấu thất bại.
   - Đẩy các dòng vào `TransformQueue`.
   - Consumer Lambda được nối với `WebQueue` trong SAM, bật partial batch response.

Các worker chỉ đọc endpoint/URL và headers từ Source do backend lưu. Không ghi headers hoặc credential vào log. Template đã mô tả kết nối worker nhưng chưa được triển khai lên AWS thật.

## Trạng thái job

- `queued`: Dispatcher vừa tạo job hoặc job đang chờ retry lỗi nguồn tạm thời.
- `normalizing`: Extractor đã thu thập thành công, ghi `row_count` và gửi dòng sang Normalizer.
- `failed`: Lỗi không thể retry hoặc đã hết 5 lần nhận; chi tiết nằm trong trường `error`, message được chuyển vào DLQ.

MVP không cam kết crawler cho website động (Single Page App yêu cầu render trình duyệt) hay giá realtime.

## Chạy demo API/Web local

Từ thư mục gốc, chạy `.venv\Scripts\python.exe -m scripts.demo_ingestion`. Demo dùng Moto và fixtures, chạy cả hai worker hai lần, đưa kết quả qua Normalizer rồi xác nhận tenant A có 6 quan sát duy nhất, tenant B có 0. Demo không gọi tài khoản AWS thật.
