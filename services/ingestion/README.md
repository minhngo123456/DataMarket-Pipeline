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
   - Hỗ trợ URL chi tiết sản phẩm có Product microdata (`meta` SKU/giá/tiền tệ) hoặc JSON-LD Product/Offer. Cấu hình `price_basis` trên nguồn làm mặc định khi HTML không có cách tính thuế; xem [hướng dẫn URL sản phẩm](../../docs/web-product-url.md).
   - Bóc tách bảng dữ liệu giá hoặc thẻ sản phẩm có `data-*` attribute bằng bộ phân tích chuẩn `HTMLTablePriceParser`.
   - Nhận dạng một số tên cột SKU/giá tiếng Việt và tiếng Anh; ưu tiên giá khuyến mãi khi đang áp dụng, chuẩn hóa cách ghi giá phổ biến và dùng thời điểm job thu thập nếu nguồn không có thời điểm.
   - Kiểm tra giới hạn dung lượng, số dòng và cô lập tenant.
   - Lỗi 429, timeout, HTTP 408/5xx và lỗi worker ngoài dự kiến được retry với backoff giới hạn; lỗi xác thực, HTTP 4xx khác và HTML sai được đánh dấu thất bại.
   - Đẩy các dòng vào `TransformQueue`.
   - Consumer Lambda được nối với `WebQueue` trong SAM, bật partial batch response.

Các worker chỉ đọc endpoint/URL và headers từ Source do backend lưu. Không ghi headers hoặc credential vào log. Ba worker đã được triển khai trong stack `marketdata-hoang-dev` ngày 01/11/2026. Luồng CSV và phát lại đã kiểm thử trên AWS; API/HTML mới được kiểm thử bằng fixtures, chưa có kết quả nguồn thật chạy đầu cuối qua UI.

API/Web worker chặn redirect HTTPS xuống HTTP trước khi gửi request tiếp theo, kể cả cùng host, để header cấu hình không bị gửi qua kết nối không mã hóa. Job thất bại với mã an toàn `source_https_downgrade_blocked`; redirect HTTPS cùng host vẫn được hỗ trợ, còn redirect sang host khác bỏ header cấu hình trừ `User-Agent`.

## Snapshot và retry của API/Web

Trước khi gửi dòng đầu tiên vào `TransformQueue`, worker lưu các dòng đã parse và kiểm tra vào một snapshot bất biến trên S3, rồi gắn snapshot và `row_count` vào job. Snapshot dùng khóa `extractor-snapshots/{tenant}/{source}/{job}.json`; prefix này nằm ngoài `tenants/` để không kích hoạt luồng CSV của RawBucket. Việc tạo snapshot có điều kiện bảo đảm các lần xử lý đồng thời cùng dùng một bộ dữ liệu.

Khi retry sau lỗi gửi SQS hoặc khi nhận lại job, worker đọc snapshot đã gắn thay vì gọi lại nguồn. Thứ tự dòng, `row_number`, `row_count`, giá và thời điểm quan sát được giữ nguyên dù nguồn đã thay đổi. Nếu snapshot đã gắn bị thiếu hoặc không hợp lệ, worker báo lỗi để retry/DLQ; không tải dữ liệu mới để thay thế. Job đã hoàn tất không bị chuyển lại về `normalizing`.

Với HTML, thời điểm do từng dòng nguồn cung cấp được ưu tiên. Dòng thiếu thời điểm dùng `scheduled_at` của job; job thủ công không có lịch dùng thời điểm được chọn một lần khi tạo snapshot. Retry đọc lại các giá trị đã lưu, không chọn lại thời điểm hiện tại.

APIWorker và WebWorker cần biến môi trường `SOURCE_SNAPSHOT_BUCKET` trỏ tới RawBucket. IAM của hai worker chỉ được `s3:GetObject`, `s3:GetObjectVersion` và `s3:PutObject` dưới `extractor-snapshots/*`; quyền `s3:ListBucket` chỉ áp dụng khi yêu cầu có prefix thuộc vùng này. Worker kiểm tra đúng khóa snapshot bằng list trước khi đọc để phân biệt chưa có snapshot với lỗi quyền truy cập. Khi đáp ứng quy tắc miễn phí hoặc có Hoàng duyệt ngoại lệ có phí theo điều kiện chi phí của dự án, cần triển khai template cùng code để có biến môi trường và quyền này; trước đó dùng Moto/local. Snapshot tuân theo vòng đời 30 ngày của RawBucket; job cũ thiếu snapshot không được tự thu thập lại để tái sử dụng cùng mã job.

## Trạng thái job

- `queued`: Dispatcher vừa tạo job hoặc job đang chờ retry lỗi nguồn tạm thời.
- `normalizing`: Extractor đã ghi `row_count` và bắt đầu chuyển dòng sang Normalizer; có thể còn retry nếu gửi SQS chưa đủ.
- `failed`: Lỗi không thể retry hoặc đã hết 5 lần nhận; chi tiết nằm trong trường `error`, message được chuyển vào DLQ.

MVP không cam kết crawler cho website động (Single Page App yêu cầu render trình duyệt) hay giá realtime.

## Trạng thái thu thập nguồn

CSV/API/Web worker ghi riêng `collection_status`, job gần nhất, thời điểm bắt đầu, thành công gần nhất và mã lỗi an toàn trên SOURCE. Chỉ ghi `succeeded` sau khi đã gửi đủ dòng vào TransformQueue; kết quả Normalizer được theo dõi riêng. Retry giữ lại mốc thành công trước, job cũ không được ghi đè trạng thái job mới. Xem [hợp đồng trạng thái nguồn và phần bàn giao API/UI](../../docs/source-collection-status.md).

## Chạy demo API/Web local

Từ thư mục gốc, chạy `.venv\Scripts\python.exe -m scripts.demo_ingestion`. Demo dùng Moto và fixtures, chạy cả hai worker hai lần, đưa kết quả qua Normalizer rồi xác nhận tenant A có 6 quan sát duy nhất, tenant B có 0. Demo không gọi tài khoản AWS thật.

Demo tự tạo bucket Moto `source-snapshots-demo` và đặt `SOURCE_SNAPSHOT_BUCKET` cho tiến trình local.

## Chạy luồng cấu hình nguồn đến ma trận ở local

Từ thư mục gốc, chạy harness và bộ kiểm thử của luồng:

```powershell
.venv\Scripts\python.exe -m scripts.demo_source_pipeline
.venv\Scripts\python.exe -m pytest tests/test_source_pipeline.py -q
```

`scripts.demo_source_pipeline` gọi API handler để tạo catalog và `POST /sources`, sau đó chạy Dispatcher theo thời điểm cố định, nhận message từ các queue Moto và chuyển qua API/Web worker → snapshot S3 Moto → `TransformQueue` → Normalizer. Cuối luồng, harness đọc trạng thái qua `GET /jobs/{job_id}/status` và giá qua `GET /matrix`, đối chiếu dữ liệu fixture và kiểm tra replay cùng kỳ lịch. Mapping SKU đã biết là dữ liệu seed của demo.

Authorizer được mô phỏng bằng claims giả; API handler vẫn tra membership, tenant và vai trò phía máy chủ. Bộ kiểm thử `tests/test_source_pipeline.py` bổ sung các tình huống cấu hình nguồn, quyền, cách ly tenant và lỗi/retry qua cùng harness. Đây không phải kiểm tra token Cognito/JWT thật.

Harness chỉ tạo boto client trong `mock_aws`, dùng credential giả, feed theo allowlist fixture và chặn DNS/TCP để không gọi HTTP hoặc AWS thật. Đầu ra là bản tóm tắt kiểm tra, không in header, credential hoặc toàn bộ response cấu hình nguồn. Việc này tuân theo quy tắc mặc định Moto/local, chi phí và bảo vệ key tại [checklist AWS](../../docs/huong-aws-readiness.md); dịch vụ bắt buộc có phí cần Hoàng duyệt trước.

Luồng local không xác minh IAM, EventBridge/Scheduler và cơ chế vận chuyển sự kiện Lambda/SQS trên AWS, token Cognito/JWT hoặc thao tác trong trình duyệt. Không dùng kết quả Moto làm bằng chứng đã nghiệm thu những phần triển khai đó.
