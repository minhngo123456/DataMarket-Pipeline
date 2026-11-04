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

Các trường `collection_job_key`, `collection_job_at`, `collection_success_key` dùng nội bộ để so thứ tự, không cần trả ra UI. Trên JOB, `collection_job_at`, `collection_started_at`, `collection_succeeded_at` chỉ được đặt lần đầu, không thay đổi do nhận lại message.

## Retry, thứ tự và tenant

- Worker kiểm tra tenant đang hoạt động, source đang bật, kind và quyền sở hữu job trước khi cập nhật. CSV còn kiểm tra bucket/key và phiên bản S3 đã gắn với job; message giả phiên bản không được đổi health.
- Thứ tự lấy từ job đã lưu phía server: `scheduled_at`, nếu thiếu dùng `created_at`; job cũ thiếu cả hai được gắn mốc một lần. Không lấy timestamp trong queue message làm thẩm quyền.
- Conditional UpdateItem ngăn job cũ thay trạng thái hiện tại của job mới. Success history được cập nhật riêng: job cũ hoàn tất muộn có thể bổ sung lịch sử khi chưa có thành công mới hơn, nhưng không làm lần chạy mới chuyển sang succeeded.
- Cùng một job đã succeeded không quay về running/retrying/failed do delivery muộn. Retry không xóa mốc thành công trước. Gửi SQS thiếu một phần phải retry trên cùng dữ liệu snapshot; chưa được ghi succeeded.
- Sau khi JOB đã có `collection_succeeded_at`, delivery lỗi muộn không được đánh dấu job failed trong lúc Normalizer còn xử lý. CSV đặt row_count/status có điều kiện để không đưa job đã completed về normalizing.
- Ghi JOB và SOURCE là hai bước độc lập. Nếu ghi health lỗi sau khi job đã failed/completed, delivery retry đối chiếu lại health bằng trạng thái đã lưu, không tải lại nguồn. Nếu ghi success history bị lỗi, retry vẫn có thể đưa lịch sử về job mới nhất sau một success cũ hoàn tất muộn.
- Lỗi tạm thời ở API/Web trả partial batch failure và backoff. Đến lượt nhận thứ 5, job/source được đánh dấu failed nếu còn đúng job đang xử lý, rồi vẫn trả failure để SQS áp dụng redrive. Moto chưa chứng minh vận chuyển sang DLQ trên AWS thật.

## Bàn giao cho API và UI

Phần worker và repository đã ghi dữ liệu, còn API/UI cần tích hợp theo hợp đồng này:

- Hiếu: bổ sung các trường công khai trên vào `GET /sources` theo tenant/RBAC hiện có; client không được tự ghi các trường health. `POST /sources` hiện có thể ghi đè source có cùng ID và `PATCH` đang đọc rồi put toàn bản ghi: cần chuyển sang cập nhật có chọn lọc các trường cấu hình để không xóa/ghi đè health khi worker đang chạy. Kiểm tra riêng cả cập nhật đồng thời và thay kind/xóa rồi tạo lại nguồn.
- Minh: hiển thị trạng thái nguồn, lần chạy/thành công gần nhất và mã lỗi; dùng job để xem số accepted/pending/rejected. Trường chưa có hiển thị “Chưa thu thập”, không suy ra lỗi hay thành công.
- Hoàng: đối chiếu quyền ControlTable và kiểm thử delivery/retry/DLQ trên AWS khi đã đủ điều kiện chi phí và triển khai trong [checklist AWS](huong-aws-readiness.md).

Không coi phần hiển thị nguồn đã hoàn tất trước khi API/UI tích hợp. Không công khai cấu hình header hoặc secret để giải thích mã lỗi.

## Kiểm tra local

```powershell
.venv\Scripts\python.exe -m pytest tests/test_source_collection.py -q
```

Bộ kiểm thử dùng Moto/fixtures; bao phủ enqueue success trước Normalizer, retry/partial send, lịch sử thành công, job cũ, delivery muộn, tenant và ràng buộc upload CSV. Tiếp tục áp dụng [điều kiện sử dụng AWS](huong-aws-readiness.md) và bảo vệ credential theo [SECURITY.md](../SECURITY.md).
