# Chuẩn bị AWS cho phần việc của Hướng

Theo [phân công nhóm](team-work-split.md), Hướng tham gia 8 giờ trong WBS 2 (hạ tầng/IAM), bên cạnh phần extractor và kiểm thử luồng ingestion. Hoàng phụ trách chính hạ tầng chung. Tài liệu này mô tả phần API/Web consumer Hướng chuẩn bị và các điều kiện cần trước khi kiểm thử triển khai; không xác nhận AWS đã được kiểm thử.

## Phần hạ tầng cần đối chiếu

Các khai báo nằm trong `infra/aws/template.yaml`:

| Thành phần | Cấu hình cần giữ |
| --- | --- |
| APIWorker / WebWorker | Runtime Python 3.12, handler tương ứng, timeout 45 giây, memory 256 MiB; mã runtime ở `build/lambda`. |
| APIQueue / WebQueue | Consumer batch 1, partial batch response, concurrency tối đa 2; visibility timeout 300 giây, DLQ riêng và `maxReceiveCount: 5`. |
| Biến môi trường | `CONTROL_TABLE`, `OBSERVATIONS_TABLE`, `TRANSFORM_QUEUE_URL`, `SOURCE_SNAPSHOT_BUCKET`; thêm `API_QUEUE_URL` hoặc `WEB_QUEUE_URL` theo worker. |
| IAM DynamoDB/SQS | Đọc/cập nhật ControlTable; nhận/xóa/đổi visibility trên đúng queue đầu vào; gửi dòng vào TransformQueue. Không mở quyền wildcard để xử lý lỗi triển khai. |
| IAM snapshot | GetObject/GetObjectVersion/PutObject chỉ dưới `extractor-snapshots/*` của RawBucket; ListBucket chỉ với prefix thuộc vùng này. |
| Snapshot/EventBridge | Khóa `extractor-snapshots/{tenant}/{source}/{job}.json` nằm ngoài `tenants/`, tránh sự kiện snapshot đi vào CSVQueue. |
| Lịch và cảnh báo | Job Dispatcher theo chu kỳ 6 giờ; `ScheduleState=DISABLED` trong khi chuẩn bị. Stack dùng chung đang bật lịch, xem [trạng thái triển khai](deployment-status.md). |

Sáu DLQ alarm theo dõi CSV, API, Web, Transform, Report và Dispatch. Cần phối hợp Hoàng xác định đích nhận, quyền và kịch bản kiểm tra; sự tồn tại của alarm trong template chưa chứng minh thông báo đã được gửi.

Collection health cập nhật SOURCE/JOB trong cùng ControlTable bằng GetItem/UpdateItem. Quyền hiện có của CSV/API/Web đã bao phủ các thao tác này; không cần thêm dịch vụ hoặc quyền wildcard.

Template là **toàn bộ stack dùng chung**, gồm bảng, bucket, API/auth và báo cáo/thông báo; không có chế độ chỉ triển khai riêng hai worker của Hướng. Việc tắt CloudFront hoặc lịch không tự chứng minh phần còn lại miễn phí.

## Chuẩn bị offline có thể làm ngay

Từ thư mục gốc, dùng môi trường Python local đã có các dependency của dự án:

```powershell
.venv\Scripts\python.exe -m scripts.stage_lambda
.venv\Scripts\cfn-lint.exe infra/aws/template.yaml
.venv\Scripts\python.exe -m scripts.validate_template
.venv\Scripts\cfn-lint.exe build/cloudformation.json
.venv\Scripts\python.exe -m pytest tests/test_sam.py -q
```

- `stage_lambda` sao chép runtime `marketdata`, `services` và `requirements.txt` vào `build/lambda`; chạy lại sau khi sửa code. Đối chiếu artifact có worker, `safe_http.py`, `snapshot.py` và `collection.py` mới nhất.
- Khi chuẩn bị artifact SAM thực tế, dùng runtime/build Python 3.12 khớp template. Kiểm thử trên phiên bản Python local khác chưa xác minh runtime triển khai.
- Kiểm tra danh sách file trong artifact: không đóng gói credential, `.env`, cấu hình cá nhân, `.aws`, `.git`, `.venv`, tests hoặc fixtures. Git ignore không tự bảo đảm các file bị ignore sẽ bị loại khỏi artifact; không in nội dung secret để kiểm tra.
- `validate_template` dùng credential giả và policy map cố định để dịch SAM offline. `build/cloudformation.json` dùng vị trí artifact giả để lint, không dùng trực tiếp cho deploy.
- Ghi lại commit, lệnh và kết quả thực tế sau khi chạy. Các lệnh trên là checklist chuẩn bị, không phải tuyên bố đã đạt kiểm tra.

Các demo local và giới hạn mô phỏng được mô tả trong [bàn giao Hướng](huong-handover.md) và [README ingestion](../services/ingestion/README.md). Chúng không xác minh IAM hoặc cơ chế vận chuyển sự kiện Lambda/SQS trên AWS thật.

### Kết quả offline ngày 04/11/2026

Kiểm tra trên nền commit `39c18f1` cùng các thay đổi local chưa commit:

- Đã chạy `scripts.stage_lambda`; audit staging có **41 file**, chỉ gồm mã Python runtime và `requirements.txt`. Hash từng file khớp nguồn hiện tại; không có `.env`, thư mục credential/cấu hình cá nhân, tests hoặc fixtures trong danh sách file staging.
- `scripts.validate_template` dịch offline thành **98 tài nguyên**. `cfn-lint` đạt trên cả `infra/aws/template.yaml` và `build/cloudformation.json`.
- Demo Moto qua API → Dispatcher → API/Web → Normalizer → API job/ma trận đạt: hai job completed, mỗi job 3 accepted; tenant A có 6 dòng, tenant B có 0. Replay giữ 6 receipts và tổng 2 lượt fetch nguồn fixture.
- `ruff check --no-cache .` đạt. Các kiểm thử của bản hiện tại được ghi tại [bàn giao Hướng](huong-handover.md).

Staging chưa phải gói đã cài dependency/build trên Lambda Python 3.12. Runtime kiểm tra local là Python 3.13.14; chưa chạy SAM build bằng Python 3.12, deploy, smoke test AWS, nguồn HTTP thật hoặc bật lịch. Không dùng CloudFormation đã dịch offline với vị trí artifact giả để triển khai.

## Điều kiện trước khi sử dụng AWS thật

Mặc định dùng Moto/local và ngân sách AWS bằng 0. Chỉ tiếp tục khi đã xác minh toàn bộ thao tác/tài nguyên liên quan không phát sinh phí, hoặc có request dịch vụ có phí được **Hoàng chấp thuận trước**, trong đúng phạm vi, hạn mức và thời gian đã duyệt. Không chạy thử để xem có tính tiền không; credit/Free Tier không thay thế việc xác minh hoặc phê duyệt.

Checklist bàn giao trước khi triển khai hoặc cập nhật:

- Chốt đúng tài khoản, Region và stack dev dùng chung; xác định tài nguyên hiện có, kể cả tài nguyên Retain. Không tự xóa hoặc tạo lại stack để bỏ qua lỗi.
- Đối chiếu quyền của người triển khai, khả năng tạo/PassRole và runtime IAM của từng worker. Quyền triển khai không thay thế việc duyệt chi phí.
- Bàn giao outputs: ControlTable, ObservationsTable, RawBucket, APIQueue/WebQueue/TransformQueue URL và ARN, APIWorker/WebWorker/Normalizer ARN, JobDispatcher ARN và HttpApiEndpoint. Các giá trị phải cùng môi trường; `SOURCE_SNAPSHOT_BUCKET` trỏ tới RawBucket của stack đó.
- Chuẩn bị nguồn API/HTML công khai được phép thu thập và cấu hình phía server. URL `.invalid` cùng feed fixture của demo chỉ dùng local. Header bí mật không được commit, in ra log hoặc chia sẻ trong request duyệt.
- Giữ `ScheduleState=DISABLED`. Sau khi đủ điều kiện chi phí, kiểm tra job có kiểm soát và đối chiếu job/receipt/observation, snapshot, retry và tenant trước khi cân nhắc bật lịch.
- Ghi riêng kết quả triển khai: commit, môi trường, job ID, trạng thái/counts và bằng chứng đã che dữ liệu nhạy cảm; phân biệt kiểm thử AWS thật với mô phỏng. Kiểm tra DLQ và đích cảnh báo khi các phần đó được cấu hình.

Nếu còn thiếu một điều kiện, tiếp tục hoàn thiện artifact, template và kịch bản local; ghi rõ phần kiểm thử AWS chưa thực hiện. Tham khảo quy trình kỹ thuật tại [infra/aws/README.md](../infra/aws/README.md), đồng thời áp dụng quy tắc chi phí và bảo mật hiện hành.
