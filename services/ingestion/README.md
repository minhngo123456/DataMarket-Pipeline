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

3. **`api_worker.py`** (Hướng triển khai):
   - Xử lý job từ `APIQueue`.
   - Thu thập dữ liệu JSON từ REST API endpoint cấu hình trong Source.
   - Bóc tách danh sách sản phẩm, kiểm tra các trường bắt buộc (`source_sku`, `price`, `currency`, `observed_at`).
   - Xử lý lỗi kết nối, HTTP 429 backoff, HTTP 401/403 và cập nhật trạng thái job (`status="failed"` khi có lỗi nguồn).
   - Đẩy các dòng vào `TransformQueue` cho Normalizer.

4. **`web_worker.py`** (Hướng triển khai):
   - Xử lý job từ `WebQueue`.
   - Thu thập HTML tĩnh được phép qua HTTP.
   - Bóc tách bảng dữ liệu giá hoặc thẻ sản phẩm có `data-*` attribute bằng bộ phân tích chuẩn `HTMLTablePriceParser`.
   - Kiểm tra giới hạn dung lượng, số dòng và cô lập tenant.
   - Đẩy các dòng vào `TransformQueue`.

MVP không cam kết crawler cho website động (Single Page App yêu cầu render trình duyệt) hay giá realtime.
