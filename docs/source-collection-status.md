# Hợp đồng trạng thái thu thập nguồn

CSV/API/Web worker ghi trạng thái vào `SOURCE#<source_id>` và mốc xử lý vào `JOB#<job_id>` trong ControlTable của đúng tenant. Không cần bảng, dịch vụ, biến môi trường hoặc quyền IAM mới ngoài quyền đọc/cập nhật ControlTable hiện có.

## Ý nghĩa và trường dữ liệu

`collection_status=succeeded` nghĩa là extractor đã đọc, kiểm tra và gửi đủ dòng sang TransformQueue. Normalizer có thể vẫn đang xử lý hoặc có dòng pending/rejected; kết quả đó được theo dõi riêng ở job và receipts. Job `normalizing` được ghi trước khi gửi SQS nên riêng trạng thái job chưa chứng minh đã gửi đủ dòng.

| Trường trên SOURCE | Ý nghĩa |
| --- | --- |
| `collection_status` | `running`, `retrying`, `succeeded` hoặc `failed` cho lần chạy hiện tại. Chưa từng chạy thì chưa có trường này. |
| `last_job_id` | Job của lần chạy hiện tại. |
| `last_attempt_at` | Thời điểm worker bắt đầu xử lý lần đầu cho job hiện tại, UTC ISO 8601; giữ nguyên qua retry của cùng job. |
| `last_success_at` | Mốc xác nhận thành công lần đầu trên SOURCE cho job thành công được ghi nhận; giữ lại khi lần chạy tiếp theo lỗi. Nếu cần retry ghi health, mốc này có thể muộn hơn `collection_succeeded_at` trên JOB. |
| `last_success_job_id`, `last_success_job_at` | Job thành công tương ứng và thời điểm logic của job đó. |
| `last_error_code` | Mã lỗi an toàn của trạng thái retry/failed; được bỏ khi bắt đầu xử lý lại hoặc thành công. Không chứa URL, header hoặc nội dung exception tùy ý. |

`GET /sources` đã được chuẩn bị trong code local để trả bảy trường công khai trong bảng khi có giá trị. Trường chưa có hoặc có giá trị null được bỏ khỏi response; client không được suy ra nguồn lỗi hay thành công khi chưa có `collection_status`.

Các trường `collection_job_key`, `collection_job_at`, `collection_success_key` dùng nội bộ để so thứ tự và không được trả qua API nguồn. `source_config_revision` cũng là trường nội bộ, do server sinh bằng UUID để kiểm soát cập nhật cấu hình đồng thời, không trả ra response hoặc nhận giá trị từ client. Trên JOB, `collection_job_at`, `collection_started_at`, `collection_succeeded_at` chỉ được đặt lần đầu, không thay đổi do nhận lại message.

## Retry, thứ tự và tenant

- Worker kiểm tra tenant đang hoạt động, source đang bật, kind và quyền sở hữu job trước khi cập nhật. CSV còn kiểm tra bucket/key và phiên bản S3 đã gắn với job; message giả phiên bản không được đổi health.
- Thứ tự lấy từ job đã lưu phía server: `scheduled_at`, nếu thiếu dùng `created_at`; job cũ thiếu cả hai được gắn mốc một lần. Không lấy timestamp trong queue message làm thẩm quyền.
- Conditional UpdateItem ngăn job cũ thay trạng thái hiện tại của job mới. Success history được cập nhật riêng: job cũ hoàn tất muộn có thể bổ sung lịch sử khi chưa có thành công mới hơn, nhưng không làm lần chạy mới chuyển sang succeeded.
- Cùng một job đã succeeded không quay về running/retrying/failed do delivery muộn. Retry không xóa mốc thành công trước. Gửi SQS thiếu một phần phải retry trên cùng dữ liệu snapshot; chưa được ghi succeeded.
- Sau khi JOB đã có `collection_succeeded_at`, delivery lỗi muộn không được đánh dấu job failed trong lúc Normalizer còn xử lý. CSV đặt row_count/status có điều kiện để không đưa job đã completed về normalizing.
- Ghi JOB và SOURCE là hai bước độc lập. Nếu ghi health lỗi sau khi job đã failed/completed, delivery retry đối chiếu lại health bằng trạng thái đã lưu, không tải lại nguồn. Nếu ghi success history bị lỗi, retry vẫn có thể đưa lịch sử về job mới nhất sau một success cũ hoàn tất muộn.
- Lỗi tạm thời ở API/Web trả partial batch failure và backoff. Đến lượt nhận thứ 5, job/source được đánh dấu failed nếu còn đúng job đang xử lý, rồi vẫn trả failure để SQS áp dụng redrive. Moto chưa chứng minh vận chuyển sang DLQ trên AWS thật.

## Bàn giao cho API và UI

Phần worker, repository và API nguồn đã được chuẩn bị local theo hợp đồng sau; chưa xác nhận tích hợp UI hoặc kiểm thử AWS thật:

- `GET /sources` trả cấu hình và các trường health công khai theo tenant/RBAC hiện có. Giá trị header chỉ trả nguyên văn cho Admin; Analyst/Viewer nhận `***`. Các response nguồn không trả revision hay trường health nội bộ.
- `POST /sources` tạo nguồn mới hoặc thay các trường cấu hình được hỗ trợ của nguồn cùng ID/cùng kind, giữ nguyên health do worker ghi. POST vẫn trả 201 khi cập nhật nguồn cùng kind. Nếu không cung cấp URL qua `endpoint`/`url`, cả hai trường được xóa; `headers` bị xóa nếu thiếu hoặc null. `name` mặc định là source ID, `enabled` mặc định true. Muốn chỉ sửa một phần, dùng PATCH.
- POST đổi kind của nguồn đã tồn tại trả HTTP 409, `error.code="conflict"`, `error.message="source_kind_conflict"`; không tự chuyển loại nguồn hoặc xóa lịch sử.
- `PATCH /sources/:id` chỉ cập nhật cấu hình được hỗ trợ, giữ health và các trường cấu hình không được gửi; `headers:null` xóa cấu hình header. POST/PATCH giữ giá trị header đã lưu nếu client gửi lại `***` cho tên header đó; tên chưa có giá trị đã lưu được bỏ qua.
- POST/PATCH dùng `UpdateItem` và revision UUID nội bộ, không put lại toàn bản ghi SOURCE. Một request cấu hình đã đọc dữ liệu cũ không được tạo lại nguồn vừa bị xóa hoặc ghi đè cấu hình vừa thay đổi/tạo lại. Xung đột trả HTTP 409, `error.code="conflict"`, `error.message="source_configuration_changed"`; client tải lại `GET /sources`, đối chiếu cấu hình rồi thực hiện lại thay đổi phù hợp. Worker ghi health không làm đổi config revision.
- Client không được ghi health, trường nội bộ hoặc chọn tenant bằng body; các trường này không được lấy từ payload cấu hình.
- Hiếu: review và tích hợp contract API nguồn vào bản dùng chung, xác nhận cách xử lý 409 và chạy các ca cập nhật đồng thời. Contract chi tiết ở [API dành cho frontend](api-contract-frontend.md).
- Minh: hiển thị trạng thái nguồn, lần chạy/thành công gần nhất và mã lỗi; dùng job để xem số accepted/pending/rejected. Trường chưa có hiển thị “Chưa thu thập”, không suy ra lỗi hay thành công.
- Hoàng: đối chiếu quyền ControlTable và kiểm thử delivery/retry/DLQ trên AWS khi đã đủ điều kiện chi phí và triển khai trong [checklist AWS](huong-aws-readiness.md).

Không coi phần hiển thị nguồn đã hoàn tất trước khi UI tích hợp và được kiểm chứng. Không công khai cấu hình header hoặc secret để giải thích mã lỗi.

## Giới hạn hiện tại

- Config revision chỉ bảo vệ thao tác cấu hình POST/PATCH; chưa thay thế định danh vòng đời nguồn. Job cũ vẫn có thể tác động health của nguồn đã bị xóa rồi tạo lại cùng source ID/cùng kind, vì job và worker chưa gắn với một source generation riêng. Đây là hạng mục lifecycle cần xử lý riêng.
- DELETE vẫn đọc rồi xóa mà chưa kiểm tra revision khi xóa; request DELETE cũ có thể xóa nguồn đã được thay đổi hoặc tạo lại sau lần đọc đó. Chưa được coi giới hạn này là đã khắc phục bằng revision của POST/PATCH.
- Snapshot chỉ cố định sau khi đã capture/lưu dữ liệu. Retry của job đã có snapshot dùng lại dữ liệu đó dù URL/header cấu hình thay đổi; job chưa có snapshot có thể dùng cấu hình mới khi fetch. Không khẳng định mọi job đã queued trước PATCH đều dùng cấu hình cũ.
- Code và kiểm thử Moto/local không chứng minh IAM, JWT thật, cơ chế vận chuyển AWS hoặc UI đã hoạt động trên môi trường triển khai.

## Kiểm tra local

```powershell
.venv\Scripts\python.exe -m pytest tests/test_source_collection.py tests/test_source_health_api.py -q
```

Bộ kiểm thử dùng Moto/fixtures; có các ca enqueue success trước Normalizer, retry/partial send, lịch sử thành công, job cũ, delivery muộn, tenant và ràng buộc upload CSV. Các ca API nguồn kiểm tra health công khai, giữ health khi cấu hình thay đổi đồng thời, revision/conflict, client giả mạo trường dữ liệu và masking header. Không dùng các ca này để khẳng định đã khắc phục những giới hạn lifecycle nêu trên. Tiếp tục áp dụng [điều kiện sử dụng AWS](huong-aws-readiness.md) và bảo vệ credential theo [SECURITY.md](../SECURITY.md).
