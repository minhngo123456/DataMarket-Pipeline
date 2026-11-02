# Hạ tầng AWS của Hoàng

[Hướng dẫn từng bước trên tài khoản cá nhân Free plan](../../docs/aws-deploy-free-plan.md).

Template: `template.yaml`, Python 3.12, AWS SAM. Hạ tầng được mô tả bằng code, chưa deploy vào tài khoản AWS.

## Có trong template

- DynamoDB ControlTable và ObservationsTable, GSI `by_day`, TTL, mã hóa và point-in-time recovery.
- S3 Raw/Reports/Web riêng tư, versioning, mã hóa, chặn HTTP. Raw gửi Object Created qua EventBridge vào CSVQueue; CloudFront dùng OAC đọc WebBucket.
- CSVQueue, APIQueue, WebQueue, TransformQueue, ReportQueue, mỗi queue có DLQ riêng. Visibility timeout 300 giây, maxReceiveCount 5. CloudWatch có cảnh báo khi DLQ có message.
- CSVWorker, APIWorker, WebWorker và Normalizer: timeout 45 giây, partial batch response, IAM giới hạn theo tài nguyên/chức năng. Các extractor dùng batch 1; Normalizer dùng batch 10. Source mapping giới hạn concurrency 2.
- Job Dispatcher mỗi 6 giờ và Report Dispatcher lúc 08:00 Asia/Ho_Chi_Minh. Scheduler có retry và DLQ; lịch mặc định DISABLED.
- Cognito user pool/client, HTTP API và JWT authorizer. `ApiFunction` (`services.api.app.handler`) đã được gắn vào 21 route của `docs/api-contract-frontend.md`, mọi route đều qua JWT authorizer; frontend đặt `VITE_API_URL` bằng output `HttpApiEndpoint` (stage `$default`, không có `/v1`). Cognito hiện chỉ cho admin tạo user.
- CloudFront/S3 Web để Minh tải giao diện. S3 CORS và API CORS cho phép WebOrigin và LocalDevOrigin (cùng mặc định http://localhost:5173). Đặt LocalDevOrigin bằng WebOrigin để chỉ cho phép một origin.

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

Chọn stack riêng cho môi trường dev, Region được nhóm chốt, capability CAPABILITY_IAM, WebOrigin đúng địa chỉ web, ScheduleState=DISABLED, EnableCloudFront=DISABLED (mặc định tắt chờ duyệt tài khoản). Chỉ bật lịch sau khi Hướng nối API/HTML consumer và Hiếu nối Aggregator. Dùng outputs để bàn giao tên bucket/table, URL/ARN queue, Cognito, authorizer và các hàm; không chép credentials vào repo.

Thử luồng CSV: API của Hiếu tạo source và job phía máy chủ, cấp presigned PUT URL cho đúng `tenants/<tenant>/raw/<job>`, trình duyệt tải UTF-8 CSV. Kiểm tra EventBridge, CSVQueue, TransformQueue, receipt/job counts và tenant isolation. Nếu chưa có API, admin có thể seed source/job bằng AWS CLI trong sandbox; không mở route tạm bỏ qua xác thực.

ReportAggregator đã đọc ReportQueue và gọi Notification trong cùng Lambda; API dùng USER_POOL_ID để tạo user Cognito khi mời thành viên. Còn thiếu SNS topic/subscription, Secrets Manager references theo tenant. APIWorker và WebWorker hiện đã được khai báo làm consumer của APIQueue/WebQueue. Cảnh báo DLQ chưa có AlarmActions; nhóm cần cấu hình kênh nhận thông báo khi triển khai.

Bucket và table có DeletionPolicy Retain để tránh mất dữ liệu khi xóa stack. Khi dọn sandbox, cần kiểm tra rồi xóa riêng tài nguyên còn giữ. CloudWatch/PITR/S3 có thể phát sinh chi phí sau khi triển khai; bản local không tạo tài nguyên AWS.

## Đưa web lên Amplify Hosting

Đang dùng cho `marketdata-dev` vì tài khoản chưa được xác minh để tạo CloudFront. App `marketdata-dev-web` (`d1v1jrtzpab44p`, ap-southeast-1) được tạo bằng CLI và triển khai thủ công, không nối Git và không nằm trong `template.yaml`. Xóa stack không xóa app này.

Cập nhật giao diện, từ thư mục gốc:

```powershell
python -m scripts.build_web_env --stack marketdata-dev --region ap-southeast-1 --profile marketdata-dev
cd apps/web
pnpm build
cd ../..
python -m scripts.deploy_web_amplify --app-id d1v1jrtzpab44p --region ap-southeast-1 --profile marketdata-dev
```

Tạo app mới cho stack khác: `aws amplify create-app --name <tên> --platform WEB --custom-rules file://rules.json`, rồi `aws amplify create-branch --app-id <appId> --branch-name main --stage PRODUCTION`. Trong `rules.json`, luật cho trang React có `status` là `200`, `target` là `/index.html`, và `source` là:

```text
</^[^.]+$|\.(?!(css|gif|ico|jpg|js|png|txt|svg|woff|woff2|ttf|map|json|webp)$)([^.]+$)/>
```

Sau đó đặt `WebOrigin=https://main.<appId>.amplifyapp.com` (không có dấu `/` cuối) khi deploy stack để API và raw bucket nhận origin này.

## Đưa web lên CloudFront

Các bước này chưa chạy thành công trên AWS thật: lần thử ngày 02/11/2026 trên `marketdata-dev` đã rollback vì tài khoản chưa được xác minh. Cần tài khoản đã được AWS xác minh cho CloudFront ([mục 8.1](../../docs/aws-deploy-free-plan.md)); nếu chưa, bật CloudFront sẽ làm bản cập nhật stack rollback. Không mở public WebBucket để né lỗi này.

1. Deploy với `EnableCloudFront=ENABLED`, các tham số khác giữ nguyên. Lấy output `WebUrl`, `WebDistributionId`, `WebBucket`.
2. Deploy lần nữa với `WebOrigin=<WebUrl>` (không có dấu `/` cuối). `LocalDevOrigin` vẫn giữ `http://localhost:5173`, nên web chạy local không bị cắt.
3. Build và tải lên, từ thư mục gốc:

```powershell
python -m scripts.build_web_env --stack marketdata-dev --region ap-southeast-1 --profile marketdata-dev
cd apps/web
pnpm build
aws s3 sync dist "s3://<WebBucket>" --delete --profile marketdata-dev --region ap-southeast-1
aws cloudfront create-invalidation --distribution-id <WebDistributionId> --paths "/*" --profile marketdata-dev
```

`--delete` xóa khỏi bucket những file không còn trong `dist`, nên kiểm tra đúng tên bucket trước khi chạy. Chạy lại bước 3 mỗi khi đổi giao diện; không có invalidation thì `index.html` cũ có thể còn trong cache CloudFront.

4. Trên Windows, kiểm tra file `.js` được tải lên với `Content-Type` dạng JavaScript, vì `aws s3 sync` đoán kiểu theo cấu hình máy. Sai kiểu thì trình duyệt không chạy được script.
5. Mở `WebUrl`: đăng nhập, tải lại một trang sâu như `/dashboard` (phải vào được trang, không báo 403), upload CSV và tạo báo cáo.

CloudFront trả `index.html` với mã 200 cho mọi lỗi 403/404, nên đường dẫn file không tồn tại cũng ra trang web chứ không báo lỗi.

## Kiểm tra sau deploy

- Hai tenant: thử job/source/product của tenant khác, membership bị vô hiệu hóa, Viewer gọi thao tác ghi và URL S3/report.
- Retry cùng file không tăng số quan sát; file bị PUT lại với phiên bản khác không được tráo vào job đã gắn phiên bản.
- Thử lỗi từng dòng và lỗi hạ tầng; kiểm tra partial batch response, DLQ, log không lộ raw record/secret.
- Thử lịch với scheduled_at cố định, nguồn bị tắt, múi giờ báo cáo và quyền từng Lambda role.
- Kiểm tra GSI trễ, retention, quota và thời gian chạy thực tế. Moto/cfn-lint chưa chứng minh các điểm này trên AWS thật.
