# Hạ tầng AWS của Hoàng

`template.yaml` mô tả backend Python 3.12 bằng AWS SAM. Stack `marketdata-hoang-dev` đã cập nhật thành công ngày 01/11/2026 với CloudFront và lịch tự động tắt. Kiểm kê 56 mục, CSV/replay, 15 kiểm tra quyền API và DLQ trên AWS đã đạt; alarm tự chuyển ALARM và gọi SNS thành công. Chi tiết và giới hạn trong [bàn giao của Hoàng](../../docs/hoang-final-handover.md). AWS Support chưa chấp thuận quyền tạo CloudFront cho tài khoản này.


Bản hợp nhất ngày 04/11/2026 thêm NotificationFunction riêng, retry từng kênh, report lock và ID quan sát theo nội dung từ `origin/main`. Các kết quả AWS ngày 01/11 dưới đây thuộc phiên bản đã triển khai lúc đó; bản hợp nhất cần được kiểm thử và triển khai riêng.

## Thành phần và giới hạn

- DynamoDB ControlTable/ObservationsTable có GSI `by_day`, TTL, mã hóa và point-in-time recovery.
- Raw/Reports/Web là các bucket riêng tư, mã hóa, có versioning và policy từ chối HTTP. Raw phát Object Created qua EventBridge tới CSVQueue.
- CSVQueue, APIQueue, WebQueue, TransformQueue và ReportQueue có DLQ riêng; Scheduler có DispatchDLQ. Sáu alarm DLQ và một alarm lỗi NotificationFunction gửi ALARM/OK tới AlarmTopic. `AlarmEmail` tạo subscription email vận hành, cần xác nhận trước khi thử cảnh báo.
- CSV/API/Web worker và Normalizer: timeout 45 giây, visibility 300 giây. ReportAggregator: timeout 120 giây, ReportQueue visibility 720 giây. Event source mapping dùng partial batch response và concurrency tối đa 2 trên mỗi mapping, dùng chung giữa các tenant.
- Job Dispatcher mỗi 6 giờ; Report Dispatcher lúc 08:00 Asia/Ho_Chi_Minh. `ScheduleState=DISABLED` mặc định; chỉ bật lịch sau khi kiểm thử nguồn, hạn mức báo cáo và retry.
- Cognito cấp token; 21 route HTTP API dùng JWT authorizer. ApiFunction kiểm tra membership/RBAC và hạn mức. IAM giới hạn tài nguyên, không thay thế kiểm tra tenant ở handler.
- ReportAggregator tiêu thụ ReportQueue, lưu báo cáo rồi gọi bất đồng bộ NotificationFunction. Hàm thông báo theo dõi kết quả riêng từng kênh và thử lại kênh chưa gửi thành công.

## CloudFront và địa chỉ giao diện

`EnableCloudFront=DISABLED` không tạo WebOAC/WebDistribution; output WebUrl cũng không xuất hiện. WebBucket vẫn riêng tư và từ chối HTTP. Chỉ quyền đọc CloudFront trong WebBucketPolicy phụ thuộc `UseCloudFront`.

`WebOrigin` là origin của giao diện; `LocalDevOrigin` cho phép kiểm thử local. Cả hai mặc định là `http://localhost:5173`. API và S3 PUT CORS dùng cùng danh sách; đặt hai tham số bằng nhau nếu chỉ cần một origin. Các thư viện Amplify/Cognito ở trình duyệt không bắt buộc dùng Amplify Hosting.

## Khóa Claude và kênh báo cáo theo tenant

Không truyền API key qua tham số CloudFormation. Tạo secret trong Secrets Manager cùng tài khoản/Region, lưu JSON có trường `api_key`, rồi truyền ARN vào `AnthropicSecretArn`. Khi để trống, runtime không gọi Claude thật. Template mặc định dành cho secret dùng khóa quản lý bởi Secrets Manager; nếu dùng KMS key riêng, cần bổ sung quyền decrypt trên key đó.

`tenant-notifications.yaml` tạo một topic và một Slack secret cho đúng một tenant. Tạo stack riêng cho từng tenant:

| Tham số | Giá trị |
| --- | --- |
| MainStackName | Tên stack backend, khớp namespace IAM/runtime |
| TenantId | `tenant_id` trong ControlTable; chữ, số, `_` hoặc `-` để SNS chấp nhận tên |
| NotificationKeyArn | Output cùng tên từ stack backend, cùng account/Region |
| Email | Email nhận báo cáo, có thể trống; phải xác nhận SNS subscription |

Lưu output `ReportTopicArn` vào `sns_topic_arn` trong META của đúng tenant. Topic có tên `${MainStackName}-tenant-${TenantId}`. `SlackSecretArn` trỏ tới `${MainStackName}/tenants/${TenantId}/slack`; secret ban đầu không có giá trị. Nhập JSON có trường `webhook_url` trong Secrets Manager trước khi gắn ARN vào `slack_secret_arn` của tenant. Không lưu webhook trực tiếp trong META hoặc log.

Template tenant không dùng Export/ImportValue. Bước bootstrap nhận outputs qua tham số. Không tự đổi MainStackName/TenantId của stack đã cấu hình: thay tên có thể tạo tài nguyên mới và để lại secret Retain. NotificationFunction chỉ có SNS Publish trong prefix của stack và GetSecretValue trong namespace Slack của stack. Aggregator chỉ đọc đúng ARN secret Claude và gọi đúng NotificationFunction. Runtime còn kiểm tra account/Region và tenant của đích gửi; không dùng kênh SNS/Slack dự phòng chung.

## Mã hóa và tài nguyên giữ lại

NotificationKey là customer-managed KMS key cho AlarmTopic và topic tenant. CloudWatch được cấp GenerateDataKey/Decrypt với SourceAccount và ARN alarm thuộc stack; role NotificationFunction được cấp quyền trên đúng key qua SNS. Topic policy từ chối HTTP. Tên các alarm dùng prefix stack để SourceArn không tạo vòng tham chiếu key/topic/alarm.

NotificationKey có Retain để stack tenant còn dùng nó không mất khóa khi stack chính bị xóa. KMS key, Secrets Manager, PITR, CloudWatch và tài nguyên giữ lại có thể tiếp tục phát sinh chi phí. Sau demo, kiểm kê stack tenant và tài nguyên Retain trước khi dọn; chỉ lên lịch xóa key khi chắc chắn không còn topic/dữ liệu cần nó. Không tự động xóa bucket, bảng hoặc secret của lần triển khai cũ.

Policy SNS liệt kê `sns:Publish` và `sns:Subscribe` để bắt buộc HTTPS. Không dùng `sns:*` trong topic policy: lần triển khai ngày 01/11/2026 bị SNS từ chối với lỗi `Policy statement action out of service scope`. cfn-lint không phát hiện lỗi này; dự án đã thêm kiểm thử hồi quy cho cả hai template. Các action hợp lệ được đối chiếu với [tài liệu SNS](https://docs.aws.amazon.com/sns/latest/dg/sns-access-policy-language-api-permissions-reference.html).

## Kiểm tra local

Từ thư mục gốc:

```powershell
.venv\Scripts\python.exe -m scripts.stage_lambda
.venv\Scripts\cfn-lint.exe infra/aws/template.yaml infra/aws/tenant-notifications.yaml
.venv\Scripts\python.exe -m scripts.validate_template
.venv\Scripts\cfn-lint.exe build/cloudformation.json
.venv\Scripts\python.exe -m pytest tests/test_infra.py tests/test_sam.py -q
```

`validate_template` dịch SAM offline, không gọi IAM/AWS. `stage_lambda` chỉ đóng gói marketdata, services và requirements; không đóng gói `.venv`, `.git`, fixture hay cấu hình cá nhân. Chạy lại sau khi sửa code.

Template chính vượt giới hạn 51.200 byte của TemplateBody truyền trực tiếp. Dùng `sam build`/`sam deploy` để đóng gói và tải template qua S3; không truyền nguyên template này vào `aws cloudformation ... --template-body`. Giới hạn template qua S3 là 1 MiB. Dịch SAM offline không chứng minh quyền hoặc điều kiện tài khoản AWS.

## Triển khai và kiểm chứng thật

Xem [hướng dẫn tài khoản Free plan](../../docs/aws-deploy-free-plan.md). Cần AWS CLI, SAM CLI, Python 3.12 và quyền IAM/KMS/SNS/Secrets Manager tương ứng. Giữ `EnableCloudFront=DISABLED`, `ScheduleState=DISABLED` khi tích hợp. Tham số tùy chọn mới là `AlarmEmail`, `AnthropicSecretArn`; không còn `AnthropicApiKey`.

Trước deploy, kiểm kê stack rollback và bucket/bảng Retain. Xem change set để biết tài nguyên tạo/thay/xóa. Giữ tên bucket/bảng dữ liệu; không tự động dọn tài nguyên của lần trước.

Sau deploy cần ghi stack status/outputs; thử CSV 100 dòng, retry, hai tenant; thử API/web với nguồn thật; xác nhận email SNS; kiểm tra DLQ, alarm và log. Kiểm thử cách ly gồm job/source/product của tenant khác, thành viên bị vô hiệu hóa, Viewer ghi dữ liệu và URL báo cáo. GSI có thể trễ; TTL xóa nền nên code vẫn lọc thời gian. Chưa có đo tải, chi phí hoặc độ trễ AWS cho bản sửa này.

cfn-lint và regression test bảo mật được cấu hình trong dự án. cfn-guard chưa được cài theo lựa chọn của người phụ trách; chưa có kết quả compliance bằng Guard. CloudFormation đã tạo change set và không báo lỗi pre-deployment validation ở lần cập nhật thứ hai; kết quả triển khai và kiểm thử runtime được ghi riêng trong tài liệu bàn giao.

## Hosting của môi trường nhóm

Các lệnh trong phần này dùng stack `marketdata-dev` tại `ap-southeast-1`, theo tài liệu của nhóm. Môi trường Hoàng đã kiểm thử là `marketdata-hoang-dev` tại `us-east-1`; cần thay đúng stack, Region và app ID nếu triển khai môi trường khác.

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

Tham khảo: [SQS/Lambda](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html), [SNS và KMS](https://docs.aws.amazon.com/sns/latest/dg/sns-key-management.html), [DynamoDB transaction IAM](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis-iam.html), [CloudFormation limits](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/cloudformation-limits.html).
