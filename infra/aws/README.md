# Hạ tầng AWS của Hoàng

Template: `template.yaml`, Python 3.12, AWS SAM. Hạ tầng được mô tả bằng code, chưa deploy vào tài khoản AWS.

## Có trong template

- DynamoDB ControlTable và ObservationsTable, GSI `by_day`, TTL, mã hóa và point-in-time recovery.
- S3 Raw/Reports/Web riêng tư, versioning, mã hóa, chặn HTTP. Raw gửi Object Created qua EventBridge vào CSVQueue; CloudFront dùng OAC đọc WebBucket.
- CSVQueue, APIQueue, WebQueue, TransformQueue, ReportQueue, mỗi queue có DLQ riêng. Visibility timeout 300 giây, maxReceiveCount 5. CloudWatch có cảnh báo khi DLQ có message.
- CSVWorker và Normalizer: timeout 45 giây, partial batch response, IAM giới hạn theo tài nguyên/chức năng. CSV dùng batch 1; Normalizer dùng batch 10. Source mapping giới hạn concurrency 2.
- Job Dispatcher mỗi 6 giờ và Report Dispatcher lúc 08:00 Asia/Ho_Chi_Minh. Scheduler có retry và DLQ; lịch mặc định DISABLED.
- Cognito user pool/client, HTTP API và JWT authorizer để Hiếu gắn route. API chưa có route nghiệp vụ; Cognito hiện cho admin tạo user.
- CloudFront/S3 Web để Minh tải giao diện. S3 CORS và API CORS dùng đúng WebOrigin, mặc định http://localhost:5173.

`ApiDataPolicyArn` là policy dữ liệu bàn giao cho Hiếu. Hạ tầng dùng chung nhiều tenant; policy giới hạn tài nguyên nhưng không tự cách ly dữ liệu theo người đăng nhập. Handler API phải kiểm tra JWT/membership/RBAC, worker đối chiếu source/job.

## Chuẩn bị và kiểm tra local

Từ thư mục gốc:

```powershell
.venv\Scripts\python.exe -m scripts.stage_lambda
.venv\Scripts\cfn-lint.exe infra/aws/template.yaml
.venv\Scripts\python.exe -m scripts.validate_template
.venv\Scripts\cfn-lint.exe build/cloudformation.json
```

`validate_template` dịch SAM offline bằng policy map cố định, không gọi IAM/AWS; kết quả build/cloudformation.json được lint riêng.

`stage_lambda` chỉ sao chép marketdata, services và requirements vào build/lambda. Không đóng gói .venv, .git, fixture hoặc file cấu hình cá nhân. Chạy lại sau khi sửa code. Thư mục build bị gitignore.

## Khi nhóm có tài khoản sandbox

Các lệnh dưới đây dành cho người phụ trách triển khai; chưa được thực hiện trong bản local này. Cần AWS CLI, SAM CLI, Python 3.12 và quyền tạo tài nguyên/IAM trong Region của nhóm.

```powershell
python -m scripts.stage_lambda
sam build --template-file infra/aws/template.yaml
sam deploy --guided
```

Chọn stack riêng cho môi trường dev, Region được nhóm chốt, capability CAPABILITY_IAM, WebOrigin đúng địa chỉ web, ScheduleState=DISABLED. Chỉ bật lịch sau khi Hướng nối API/HTML consumer và Hiếu nối Aggregator. Dùng outputs để bàn giao tên bucket/table, URL/ARN queue, Cognito, authorizer và các hàm; không chép credentials vào repo.

Thử luồng CSV: API của Hiếu tạo source và job phía máy chủ, cấp presigned PUT URL cho đúng `tenants/<tenant>/raw/<job>`, trình duyệt tải UTF-8 CSV. Kiểm tra EventBridge, CSVQueue, TransformQueue, receipt/job counts và tenant isolation. Nếu chưa có API, admin có thể seed source/job bằng AWS CLI trong sandbox; không mở route tạm bỏ qua xác thực.

Hiếu bổ sung Aggregator/Notification và SNS topic/subscription, Secrets Manager references theo tenant. Hướng bổ sung consumer APIQueue/WebQueue. Các phần đó chưa nằm trong template bằng handler giả. Cảnh báo DLQ chưa có AlarmActions; nhóm cần cấu hình kênh nhận thông báo khi triển khai.

Bucket và table có DeletionPolicy Retain để tránh mất dữ liệu khi xóa stack. Khi dọn sandbox, cần kiểm tra rồi xóa riêng tài nguyên còn giữ. CloudWatch/PITR/S3 có thể phát sinh chi phí sau khi triển khai; bản local không tạo tài nguyên AWS.

## Kiểm tra sau deploy

- Hai tenant: thử job/source/product của tenant khác, membership bị vô hiệu hóa, Viewer gọi thao tác ghi và URL S3/report.
- Retry cùng file không tăng số quan sát; file bị PUT lại với phiên bản khác không được tráo vào job đã gắn phiên bản.
- Thử lỗi từng dòng và lỗi hạ tầng; kiểm tra partial batch response, DLQ, log không lộ raw record/secret.
- Thử lịch với scheduled_at cố định, nguồn bị tắt, múi giờ báo cáo và quyền từng Lambda role.
- Kiểm tra GSI trễ, retention, quota và thời gian chạy thực tế. Moto/cfn-lint chưa chứng minh các điểm này trên AWS thật.
