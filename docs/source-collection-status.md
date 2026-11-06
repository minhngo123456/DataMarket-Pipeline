# Hợp đồng trạng thái thu thập nguồn

CSV/API/Web worker ghi trạng thái vào `SOURCE#<source_id>` và mốc xử lý vào `JOB#<job_id>` trong ControlTable của đúng tenant. Không thêm bảng, dịch vụ hoặc biến môi trường; quyền worker giữ nguyên. Normalizer được bổ sung quyền `dynamodb:ConditionCheckItem` chỉ trên ControlTable để kiểm tra generation cùng transaction ghi dữ liệu; API đã có quyền này.

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

Các trường `collection_job_key`, `collection_job_at`, `collection_success_key` dùng nội bộ để so thứ tự và không được trả qua API nguồn. `source_config_revision` do server sinh bằng UUID để kiểm soát cập nhật cấu hình đồng thời; `source_generation` xác định vòng đời nguồn. Cả hai là trường nội bộ, không trả trong response nguồn hoặc nhận giá trị từ client. Trên JOB, `collection_job_at`, `collection_started_at`, `collection_succeeded_at` chỉ được đặt lần đầu, không thay đổi do nhận lại message.

## Vòng đời nguồn

- Server sinh `source_generation` UUID khi tạo nguồn mới. POST/PATCH trên nguồn đang tồn tại giữ nguyên generation; xóa rồi tạo lại cùng ID, kể cả cùng kind, sinh generation khác.
- Job CSV/API/Web lưu generation từ nguồn phía server khi tạo. Worker đối chiếu job với nguồn hiện tại trước khi thu thập; cập nhật trạng thái hiện tại và lịch sử thành công trên SOURCE đều có điều kiện generation. Job của nguồn cũ không được ghi health vào nguồn vừa tạo lại.
- Dispatcher đưa generation vào job ID theo kỳ lịch của nguồn mới, nên tạo lại nguồn trong cùng kỳ không tái dùng job của nguồn trước. Nguồn legacy và job legacy cùng thiếu generation tiếp tục hoạt động khi nguồn legacy đó còn tồn tại; chỉnh cấu hình không tự gắn generation mới. Khi xóa/tạo lại qua API, nguồn nhận UUID mới và job legacy bị chặn. Không tự gán generation hiện tại cho job cũ trong lúc retry.
- Normalizer lấy generation từ job đã lưu, kiểm tra nguồn cùng transaction ghi receipt/observation. Message Transform được xác định thuộc generation cũ được ACK mà không ghi thêm dữ liệu vào nguồn mới; các receipt và lịch sử đã lưu được giữ lại. Pending receipt của generation cũ không được duyệt vào nguồn tạo lại.
- Mỗi lần thêm receipt hoặc duyệt/từ chối SKU tăng `receipts_revision` nội bộ trên job trong cùng transaction. Refresh đọc job trước khi đếm receipts và chỉ ghi counts khi revision, trạng thái và `row_count` vẫn khớp; có thay đổi thì đọc/đếm lại. Lượt refresh cũ không được ghi lùi counts hoặc mở lại job đã hoàn tất/thất bại/hủy. API job vẫn giữ định dạng response hiện tại.
- Mapping SKU vẫn theo `source_id` và được giữ khi xóa nguồn; nguồn tạo lại cùng ID có thể tiếp tục dùng mapping đã có. Thay đổi này không xóa mapping hoặc lịch sử giá. Generation được lưu nội bộ trên job/receipt; schema envelope Transform và snapshot vẫn giữ nguyên.

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

- `GET /sources` trả cấu hình và các trường health công khai theo tenant/RBAC hiện có. Giá trị header chỉ trả nguyên văn cho Admin; Analyst/Viewer nhận `***`. Các response nguồn không trả generation, revision hay trường health nội bộ.
- `POST /sources` tạo nguồn mới hoặc thay các trường cấu hình được hỗ trợ của nguồn cùng ID/cùng kind, giữ nguyên health do worker ghi. POST vẫn trả 201 khi cập nhật nguồn cùng kind. Nếu không cung cấp URL qua `endpoint`/`url`, cả hai trường được xóa; `headers` bị xóa nếu thiếu hoặc null. `name` mặc định là source ID, `enabled` mặc định true. Muốn chỉ sửa một phần, dùng PATCH.
- Nếu POST/PATCH có `enabled`, giá trị phải là JSON boolean; giá trị sai trả 400 `invalid_source_payload` mà không ghi cấu hình. Tạo/xóa nguồn được tuần tự hóa bằng revision danh sách nguồn trên META trong cùng transaction; quota kiểm tra trên số nguồn thực tế, kể cả nguồn đã tắt, và không cần counter cho dữ liệu legacy. Request tạo vượt quota trả 429, còn chỉnh nguồn cùng ID không chiếm thêm suất.
- POST đổi kind của nguồn đã tồn tại trả HTTP 409, `error.code="conflict"`, `error.message="source_kind_conflict"`; không tự chuyển loại nguồn hoặc xóa lịch sử.
- `PATCH /sources/:id` chỉ cập nhật cấu hình được hỗ trợ, giữ health và các trường cấu hình không được gửi; `headers:null` xóa cấu hình header. POST/PATCH giữ giá trị header đã lưu nếu client gửi lại `***` cho tên header đó; tên chưa có giá trị đã lưu được bỏ qua.
- POST/PATCH dùng `UpdateItem` và revision UUID nội bộ, không put lại toàn bản ghi SOURCE. Một request cấu hình đã đọc dữ liệu cũ không được tạo lại nguồn vừa bị xóa hoặc ghi đè cấu hình vừa thay đổi/tạo lại. Xung đột trả HTTP 409, `error.code="conflict"`, `error.message="source_configuration_changed"`; client tải lại `GET /sources`, đối chiếu cấu hình rồi thực hiện lại thay đổi phù hợp. Worker ghi health không làm đổi config revision.
- `DELETE /sources/:id` chỉ xóa nếu generation và config revision vẫn khớp bản server đã đọc; kiểm tra cùng thao tác xóa có điều kiện. Nguồn bị sửa/xóa/tạo lại trong khoảng đọc đến xóa trả cùng lỗi HTTP 409 `source_configuration_changed`. Nếu nguồn chưa tồn tại khi server đọc, trả 404 `source_not_found`. Worker chỉ ghi health không làm DELETE conflict.
- Client không được ghi health, trường nội bộ hoặc chọn tenant bằng body; các trường này không được lấy từ payload cấu hình.
- Hiếu: review và tích hợp contract API nguồn vào bản dùng chung, xác nhận cách xử lý 409 và chạy các ca cập nhật đồng thời. Contract chi tiết ở [API dành cho frontend](api-contract-frontend.md).
- Minh: hiển thị trạng thái nguồn, lần chạy/thành công gần nhất và mã lỗi; dùng job để xem số accepted/pending/rejected. Trường chưa có hiển thị “Chưa thu thập”, không suy ra lỗi hay thành công.
- Hoàng: đối chiếu quyền ControlTable và kiểm thử delivery/retry/DLQ trên AWS khi đã đủ điều kiện chi phí và triển khai trong [checklist AWS](huong-aws-readiness.md).

Không coi phần hiển thị nguồn đã hoàn tất trước khi UI tích hợp và được kiểm chứng. Không công khai cấu hình header hoặc secret để giải thích mã lỗi.

## Giới hạn hiện tại

- Generation và revision bảo vệ theo hợp đồng các thao tác server; revision vẫn chưa phát hiện một form đã cũ từ trước khi request đến server.
- Trong cùng generation, snapshot chỉ cố định sau khi đã capture/lưu dữ liệu. Retry của job đã có snapshot dùng lại dữ liệu đó dù URL/header cấu hình thay đổi; job chưa có snapshot có thể dùng cấu hình mới khi fetch. Không khẳng định mọi job đã queued trước PATCH đều dùng cấu hình cũ. Job khác generation bị chặn thay vì đọc cấu hình hoặc replay snapshot vào nguồn mới.
- Code và kiểm thử Moto/local không chứng minh IAM, JWT thật, cơ chế vận chuyển AWS hoặc UI đã hoạt động trên môi trường triển khai.

## Kiểm tra local

```powershell
build\venv312\Scripts\python.exe -m pytest tests/test_source_collection.py tests/test_source_health_api.py tests/test_source_lifecycle.py tests/test_source_delete_concurrency.py -q
```

Bộ kiểm thử dùng Moto/fixtures; có các ca enqueue success trước Normalizer, retry/partial send, lịch sử thành công, job cũ, delivery muộn, tenant và ràng buộc upload CSV. Các ca API nguồn kiểm tra health công khai, giữ health khi cấu hình thay đổi đồng thời, revision/conflict, client giả mạo trường dữ liệu và masking header. Các ca hồi quy lifecycle kiểm tra job cũ sau khi xóa/tạo lại nguồn, ghi Normalizer có điều kiện và DELETE gặp thay đổi cấu hình đồng thời, gồm nguồn legacy. Kết quả local không thay thế nghiệm thu UI/AWS. Tiếp tục áp dụng [điều kiện sử dụng AWS](huong-aws-readiness.md) và bảo vệ credential theo [SECURITY.md](../SECURITY.md).
