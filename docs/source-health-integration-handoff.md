# Bàn giao trạng thái nguồn của Hướng cho API và frontend

Phần worker thu thập có thể kiểm tra độc lập với frontend. Bản thay đổi local bổ sung dữ liệu trạng thái vào `GET /sources` và bảo vệ trạng thái đó khi sửa cấu hình nguồn. Hiếu review phần API trước khi hợp nhất; Minh có thể dùng ví dụ local để tích hợp song song. Đây chưa phải kết quả nghiệm thu giao diện hoặc AWS thật.

## Chạy ngay ở local

Dùng Python 3.12 và dependencies trong `requirements-dev.lock.txt`. Môi trường 3.12 đã chuẩn bị trên máy hiện tại nằm ở `build/venv312`.

```powershell
build\venv312\Scripts\python.exe -m scripts.demo_source_health --output build/evidence/source-health-api-20261105/api-examples.json
build\venv312\Scripts\python.exe -m pytest tests/test_source_health_api.py tests/test_source_collection.py tests/test_source_pipeline.py tests/test_source_lifecycle.py tests/test_source_delete_concurrency.py tests/test_api.py -q
```

Demo dùng Moto và fixture, chặn kết nối mạng trong pipeline, tự thay credential bằng giá trị giả. Không cần đăng nhập AWS. URL `.example.invalid` trong ví dụ chỉ là địa chỉ giả. Không dùng chúng làm nguồn thật.

File JSON có phản hồi thực của handler `GET /sources` theo vai trò Viewer cho bảy tình huống: chưa thu thập, đang chạy, đang retry, đã enqueue thành công nhưng Normalizer chưa xong, sau khi sửa cấu hình, job mới retry có lịch sử thành công, và job lỗi có lịch sử thành công. Có thêm kết quả job hoàn tất: mỗi nguồn API/Web nhận 3 accepted, 0 pending, 0 rejected; ma trận có 6 dòng, tenant khác có 0 nguồn.

`fixture_clock` cố định cửa sổ quan sát của dữ liệu mẫu; các mốc health dùng đồng hồ tiến trình. Bộ ví dụ này phục vụ kiểm tra contract/UI. Bộ dữ liệu gửi Hoàng chạy nghiệm thu AWS trước đó được giữ riêng, không thay thế bằng các URL giả này.

## Minh dùng dữ liệu nào

Đọc danh sách từ `data`; `sources` là alias cùng nội dung. Tất cả trường health đều tùy chọn, dùng thời gian UTC ISO 8601.

| Dữ liệu | Cách hiển thị/đối chiếu |
| --- | --- |
| Không có `collection_status` | Chưa thu thập; không tự suy ra thành công hoặc lỗi. |
| `running` | Đang thu thập. |
| `retrying` | Đang chờ thử lại; có thể kèm `last_error_code`. |
| `succeeded` | Thu thập xong và đã gửi đủ dòng sang TransformQueue. |
| `failed` | Thu thập lỗi; hiển thị mã lỗi an toàn. |
| `last_job_id`, `last_attempt_at` | Job hiện tại và lần bắt đầu đầu tiên của job đó; thời điểm không tăng sau mỗi retry. |
| `last_success_at`, `last_success_job_id`, `last_success_job_at` | Lịch sử thành công; giữ nguyên khi job mới đang chạy hoặc lỗi. |
| `enabled=false` | Hiển thị nguồn đã tắt riêng với kết quả thu thập trước đó. |

`succeeded` chưa chứng minh dữ liệu đã được Normalizer chấp nhận. Với Admin/Analyst, đối chiếu `GET /jobs/{last_job_id}/status`: `status`, `row_count`, `counts.accepted/pending/rejected`, sau đó đối chiếu `/matrix`. Viewer đọc health nguồn được nhưng không có quyền đọc endpoint job hiện tại; không gọi endpoint đó bằng Viewer.

Sau POST/PATCH, đọc lại `GET /sources` để nhận health; response ghi cấu hình vẫn giữ định dạng cũ. Không gửi health, tenant, generation hoặc revision từ UI. Header vẫn theo quyền hiện có: Analyst/Viewer nhận `***`; không đưa secret thật vào fixture, ảnh chụp hoặc log chia sẻ.

## Hiếu review điểm nào

- GET chỉ thêm bảy trường health công khai đã có giá trị; không trả khóa DynamoDB, khóa thứ tự worker, `source_generation` hoặc `source_config_revision`.
- POST cùng ID/kind vẫn trả 201 và thay cấu hình hỗ trợ; endpoint/url/headers bị bỏ khỏi payload POST sẽ bị xóa khỏi cấu hình. Dùng PATCH để sửa từng phần. Cả hai giữ nguyên health, kể cả worker cập nhật sau lúc API đọc nguồn.
- POST đổi kind của ID đang tồn tại trả 409, `error.code=conflict`, `error.message=source_kind_conflict`. PATCH không hỗ trợ đổi kind. Không hướng dẫn xóa/tạo lại nguồn đang có job dở để lách lỗi này.
- Nếu cấu hình thay đổi, bị xóa hoặc thay thế trong lúc request POST/PATCH/DELETE đang xử lý: 409, `error.code=conflict`, `error.message=source_configuration_changed`. DELETE kiểm tra generation và revision cùng thao tác xóa; worker chỉ ghi health không gây conflict. UI tải lại danh sách để người dùng xem và gửi lại ý định sửa/xóa; không tự lặp vô hạn request cũ. Nguồn chưa tồn tại khi DELETE đọc trả 404 `source_not_found`.
- Revision do server sinh, không yêu cầu UI gửi. Nó bảo vệ thời gian từ lúc server đọc đến lúc ghi, chưa phát hiện một form đã cũ từ trước khi request đến server.
- Generation UUID do server sinh khi tạo nguồn mới, giữ nguyên khi POST/PATCH nguồn hiện có. Xóa/tạo lại cùng ID nhận generation mới; job cũ không được fetch, ghi health hoặc thêm dữ liệu vào nguồn mới. Nguồn/job legacy cùng thiếu generation tiếp tục chạy khi nguồn legacy còn tồn tại; job legacy không được tự nhận generation của nguồn tạo lại.
- Dispatcher đưa generation vào job ID của nguồn mới, kể cả khi tạo lại trong cùng kỳ lịch. Normalizer kiểm tra generation cùng transaction ghi dữ liệu; message Transform thuộc generation cũ được ACK mà không ghi thêm. Mapping theo source ID và lịch sử đã lưu vẫn giữ; pending receipt của nguồn cũ không được duyệt vào nguồn tạo lại. Schema envelope Transform và snapshot không đổi.
- Giữ quota, tenant/RBAC, masking header và khôi phục `***` như hiện tại. Không thêm tài nguyên hoặc biến môi trường; quyền worker giữ nguyên. Normalizer được bổ sung `dynamodb:ConditionCheckItem` chỉ trên ControlTable cho kiểm tra generation trong transaction; API đã có quyền này.

## Việc còn lại để nghiệm thu phần của Hướng

1. Hiếu review/hợp nhất API; Minh nối trạng thái nguồn và xử lý trường vắng mặt/409 theo bảng trên.
2. Hướng cùng Minh kiểm tra UI bằng ví dụ local: dữ liệu nguồn, lịch sử thành công sau retry/lỗi và job/ma trận tương ứng.
3. Nhận kết quả Hoàng chạy AWS trong phạm vi chi phí được xác nhận/chấp thuận: job ID, counts, số dòng ma trận, log lỗi đã che thông tin nhạy cảm. Đối chiếu bộ `expected-results.json` đã gửi Hoàng và [checklist AWS của Hướng](huong-aws-readiness.md).

Hiếu/Hoàng cần review các ca hồi quy generation và DELETE trên bản tích hợp; đây là thay đổi code/local, chưa phải nghiệm thu UI hoặc AWS. Trong cùng generation, snapshot cố định sau khi capture; job chưa có snapshot có thể đọc cấu hình mới. Retry dùng snapshot đã gắn với job vẫn giữ dữ liệu cũ, còn job khác generation bị chặn sau khi xóa/tạo lại nguồn. Hợp đồng vòng đời nguồn, tương thích legacy và giới hạn form cũ được mô tả trong [hợp đồng trạng thái nguồn](source-collection-status.md).
