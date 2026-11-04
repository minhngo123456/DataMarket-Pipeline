# Triển khai và kiểm tra phần của Hoàng trên AWS

Các lệnh dưới đây dùng PowerShell, stack `marketdata-hoang-dev`, profile `marketdata-dev`, Region `us-east-1`. Đổi đồng bộ nếu nhóm dùng môi trường khác. Chạy từ thư mục gốc của repo. Không cần push GitHub để triển khai bản local.

AWS Support đã từ chối quyền tạo CloudFront cho tài khoản đang dùng. Vì vậy giữ `EnableCloudFront=DISABLED`: template không tạo distribution/OAC và các tham chiếu của chúng. Trên stack này giao diện chạy cục bộ; backend vẫn triển khai trên AWS. Stack dùng chung `marketdata-dev` tại `ap-southeast-1` có giao diện trên Amplify; cấu hình và kết quả của stack đó nằm trong [trạng thái triển khai](deployment-status.md).

## 1. Xác nhận tài khoản và trạng thái hiện tại

```powershell
aws sts get-caller-identity --profile marketdata-dev --region us-east-1
.venv\Scripts\python.exe -m scripts.verify_aws --profile marketdata-dev --region us-east-1 --stack marketdata-hoang-dev --output build/evidence/aws-before-update.json
```

Nếu hết phiên, đăng nhập lại bằng `aws login --profile marketdata-login` rồi kiểm tra profile `marketdata-dev`. Profile này có thể dùng `credential_process` gọi `aws configure export-credentials --profile marketdata-login --format process`; không in hoặc chép credential vào repo/chat. Các giá trị credit và ngày hết hạn phải xem trực tiếp trong Billing; không dùng số từ ảnh chụp cũ để kết luận ngân sách còn lại.

`verify_aws` chỉ đọc. Nó lưu tài khoản, Region, thời gian, trạng thái stack, toàn bộ các trang danh sách tài nguyên và kiểm tra cấu hình. Nó không đọc giá trị secret, không gọi AI, không nhận message SQS và không đổi tài nguyên. `status=needs_attention` có thể chỉ ra stack cũ thiếu thành phần mới, chưa phải lỗi ở worker. `status=passed` chỉ nói các kiểm tra cấu hình đã đạt; chưa chứng minh đăng nhập, IAM thực tế, email hoặc pipeline đầu cuối.

Nếu stack là `ROLLBACK_COMPLETE`, JSON giữ danh sách `DELETE_SKIPPED` để kiểm kê. Không deploy cập nhật hoặc xóa dữ liệu theo một lệnh dọn chung. Đối chiếu StackId, bucket và bảng được giữ lại trước khi chọn tạo môi trường mới hay nhập lại tài nguyên. Nếu stack đang `CREATE_COMPLETE`/`UPDATE_COMPLETE`, ưu tiên cập nhật nó bằng change set, không xóa để làm lại.

## 2. Kiểm tra và đóng gói bản local

```powershell
.venv\Scripts\python.exe -m pytest -q --basetemp build/pytest
.venv\Scripts\ruff.exe check .
.venv\Scripts\python.exe -m scripts.stage_lambda
.venv\Scripts\python.exe -m scripts.validate_template
.venv\Scripts\cfn-lint.exe infra/aws/template.yaml infra/aws/tenant-notifications.yaml build/cloudformation.json
$projectPython = (Resolve-Path '.venv\Scripts').Path
$env:PATH = "$projectPython;$env:PATH"
sam build --template-file infra/aws/template.yaml
```

Đợi `Build Succeeded`. `build/cloudformation.json` là bản dịch để lint offline, không phải artifact dùng deploy. `stage_lambda` chỉ đưa mã runtime và requirements vào `build/lambda`, không đóng gói `.venv`, `.env`, fixture hoặc Git.

## 3. Tạo change set và triển khai

```powershell
sam deploy --guided --profile marketdata-dev --region us-east-1 --stack-name marketdata-hoang-dev --capabilities CAPABILITY_IAM --parameter-overrides WebOrigin=http://localhost:5173 ScheduleState=DISABLED EnableCloudFront=DISABLED
```

Giữ `Confirm changes before deploy=Y` và `Disable rollback=N`. Rà tài nguyên thay đổi/thay thế trước khi xác nhận. Không tiếp tục nếu change set định thay thế bucket/bảng đang có dữ liệu mà nhóm chưa chốt cách bảo toàn dữ liệu.

- `AlarmEmail`: email nhận cảnh báo thử nghiệm; người nhận phải xác nhận subscription SNS. Có AlarmActions chưa có nghĩa email đã được nhận.
- `AnthropicSecretArn`: để trống khi chưa thử AI. Khi cần gọi thật, tạo secret JSON `{"api_key":"..."}` bằng Console hoặc phương thức nhập bảo mật rồi truyền ARN. Không truyền API key bằng tham số deploy hoặc ghi key trong PowerShell history.
- `ScheduleState`: giữ `DISABLED` trong giai đoạn kiểm thử. Lịch nguồn mỗi 6 giờ và lịch báo cáo 08:00 chỉ được bật sau khi luồng tương ứng đã được kiểm chứng.
- `EnableCloudFront`: giữ `DISABLED`. AWS chưa chấp thuận quyền CloudFront; không xem việc tăng quyền IAM, đổi Region hoặc nâng gói là cách khắc phục đã được bảo đảm.

Sau khi stack hoàn tất:

```powershell
.venv\Scripts\python.exe -m scripts.verify_aws --profile marketdata-dev --region us-east-1 --stack marketdata-hoang-dev --output build/evidence/aws-after-update.json
aws cloudformation describe-stacks --stack-name marketdata-hoang-dev --profile marketdata-dev --region us-east-1 --query 'Stacks[0].Outputs' --output table
```

Preflight kiểm tra bucket riêng tư/mã hóa, Raw versioning, redrive policy, consumer SQS, partial batch response, visibility timeout theo từng Lambda, lịch tắt, AlarmActions và việc không bật header test của API. Nó không xuất các biến môi trường của Lambda. Nếu không đủ quyền đọc một dịch vụ, báo cáo thất bại và giữ mã lỗi, không mặc định đánh dấu đạt.

## 4. Kiểm thử CSV và xử lý trùng

Lệnh này **ghi dữ liệu AWS thật**, tạo hai tenant fixture mới, một job CSV và một phiên bản S3. Tài khoản thực hiện cần quyền quản trị fixture trong sandbox và gọi Lambda kiểm thử. Nó không dùng tài khoản người dùng cuối và không chứng minh Cognito/UI đã hoạt động.

```powershell
.venv\Scripts\python.exe -m scripts.smoke_test_aws --profile marketdata-dev --region us-east-1 --stack marketdata-hoang-dev --write-fixtures --output build/evidence/csv-smoke.json
```

Script luôn tạo fixture mới, không lấy kết quả của job cũ để tuyên bố đạt. Thời điểm quan sát được cập nhật theo ngày chạy để dữ liệu không hết hạn.

1. Đẩy CSV 100 dòng vào S3 Raw, chờ EventBridge → CSVWorker → TransformQueue → Normalizer.
2. Đọc job và cả hai phân vùng tenant: cần `accepted=90`, `rejected=10`, `pending=0`; tenant A có 90 quan sát, tenant B có 0.
3. Gọi lại CSV Lambda đã triển khai với **cùng VersionId**; không PUT object mới. Gọi đồng bộ Normalizer bằng 10 lô, đủ 100 dòng trùng, rồi đọc lại số liệu. Như vậy phép kiểm tra không dựa vào số message SQS ước tính hoặc trạng thái completed cũ để kết luận retry đã được xử lý.
4. Lưu thời gian, tài khoản, stack, fixture và kết quả trước/sau vào JSON. Nếu lỗi, JSON có `status=failed`; không chuyển lỗi thành kết quả thành công.

Kết quả này kiểm tra dữ liệu theo tenant ở tầng xử lý. Nó không thay thế kiểm thử JWT, RBAC, link báo cáo, truy cập chéo tenant qua HTTP API, API/Web worker hoặc gửi thông báo; các mục đó được ghi `not_run`.

Sự kiện S3 → EventBridge mới bật có thể mất khoảng 5 phút để có hiệu lực. Nếu vừa tạo stack và hết thời gian chờ, đọc log trước khi tạo thêm fixture; có thể chạy lại với `--timeout 600` sau khi xác định nguyên nhân.

## 5. Kiểm tra DLQ thật, không đổi cấu hình retry

Chạy sau khi xác nhận môi trường dev đang rảnh. Tùy chọn này thêm một envelope sai schema có marker riêng vào TransformQueue. Normalizer từ chối envelope đó; SQS tự đưa nó sang TransformQueueDLQ sau số lần nhận đã cấu hình.

```powershell
.venv\Scripts\python.exe -m scripts.smoke_test_aws --profile marketdata-dev --region us-east-1 --stack marketdata-hoang-dev --write-fixtures --check-dlq --dlq-timeout 1800 --output build/evidence/csv-dlq-smoke.json
```

Với visibility timeout 300 giây và maxReceiveCount 5, bước này có thể kéo dài khoảng 25 phút hoặc hơn. Script không giảm timeout/đổi redrive policy để làm kiểm thử chạy nhanh hơn. Nó nhận message ở DLQ với visibility 0, chỉ tìm đúng marker của mình, không xóa hoặc giữ ẩn message khác. Marker được ghi vào JSON ngay sau khi gửi, kể cả khi bước chờ chưa hoàn tất.

Chỉ ghi nhận DLQ đạt khi marker thực sự xuất hiện trong đúng DLQ. `configured_max_receive_count=5` là cấu hình đã đọc; `receive_count` là giá trị AWS trả về, không phải số retry suy đoán. Giữ message kiểm thử để chụp bằng chứng rồi xóa đúng message đó trong Console khi không còn cần. Kiểm tra alarm và email thực nhận riêng; việc nhìn thấy message không chứng minh kênh cảnh báo đã gửi thành công.

Alarm hiện dùng `Period=300` và `EvaluationPeriods=1`. Sau khi message vào DLQ, kiểm tra metric `ApproximateNumberOfMessagesVisible`, đợi chu kỳ đánh giá rồi đọc trạng thái và lịch sử hành động:

```powershell
aws cloudwatch describe-alarms --alarm-names marketdata-hoang-dev-TransformQueueDLQAlarm --profile marketdata-dev --region us-east-1 --query 'MetricAlarms[].{State:StateValue,Reason:StateReason}' --output table
aws cloudwatch describe-alarm-history --alarm-name marketdata-hoang-dev-TransformQueueDLQAlarm --history-item-type Action --profile marketdata-dev --region us-east-1 --query 'AlarmHistoryItems[].{Time:Timestamp,Result:HistorySummary}' --output table
```

`Successfully executed action` xác nhận CloudWatch gọi SNS thành công; vẫn phải kiểm tra subscription và hộp thư để xác nhận email. Nếu queue đã ngủ lâu, [metric SQS có thể trễ khi hoạt động lại](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/monitoring-using-cloudwatch.html); đối chiếu metric thật trước khi kết luận alarm bị lỗi.

## 6. Đăng nhập thật và hai tenant

Có thể kiểm tra tự động bằng Cognito SRP và HTTP API thật sau khi cài dependency frontend (`cd apps/web`, `pnpm install --frozen-lockfile`, rồi về thư mục gốc):

```powershell
.venv\Scripts\python.exe -m scripts.smoke_api_aws --profile marketdata-dev --region us-east-1 --stack marketdata-hoang-dev --write-fixtures --cleanup --output build/evidence/aws-api-smoke.json
```

Node.js phải có trên PATH; nếu cần, truyền `--node` với đường dẫn executable. Script dùng thư viện Amplify của web, không thay auth flow của app client hoặc bật header xác thực thử nghiệm. Nó tạo ba Cognito user với `MessageAction=SUPPRESS`, không gửi email; mật khẩu ngẫu nhiên chỉ đi qua stdin trong tiến trình. Mười lăm phép kiểm tra gồm JWT hợp lệ/sai, vai trò Viewer, truy cập chéo job/report, tenant giả trong body và thu hồi quyền dù JWT còn hiệu lực. `--cleanup` xóa đúng user/partition do lần chạy tạo. Đây là kiểm tra API bằng thư viện đăng nhập thật, chưa phải thao tác trình duyệt đầu cuối.

Tạo các user thử nghiệm riêng trong Cognito User Pool của stack; dùng chức năng gửi lời mời của Console, không đưa mật khẩu vào command line. Lấy `sub` của từng tài khoản. Bootstrap một tenant và Admin đầu tiên bằng thao tác quản trị có điều kiện. Ví dụ dưới đây chỉ dành cho **tenant mới**; thay email và `sub` bằng tài khoản Cognito vừa tạo:

```powershell
@'
import boto3
from boto3.dynamodb.conditions import Attr
from marketdata.repository import DynamoStore

session = boto3.Session(profile_name="marketdata-dev", region_name="us-east-1")
stack = session.client("cloudformation").describe_stacks(StackName="marketdata-hoang-dev")["Stacks"][0]
outputs = {x["OutputKey"]: x["OutputValue"] for x in stack["Outputs"]}
store = DynamoStore(outputs["ControlTable"], outputs["ObservationsTable"],
                   resource=session.resource("dynamodb"), client=session.client("dynamodb"))
tenant = "pilot-a"
store.control.put_item(
    Item={"pk": "T#" + tenant, "sk": "META", "status": "active", "product_count": 0},
    ConditionExpression=Attr("pk").not_exists())
store.save_member(tenant, "REPLACE_WITH_COGNITO_SUB", "admin", "replace@example.com")
'@ | .venv\Scripts\python.exe -
```

Nếu tenant đã tồn tại, không chạy lại lệnh tạo META hoặc dùng PutItem đè lên nó. Kiểm tra tenant trước, rồi gọi riêng `save_member` khi cần; helper cập nhật hai bản ghi membership bằng transaction, không cho chuyển `sub` sang tenant khác. Không dùng tên/email tự suy ra làm khóa danh tính.

Minh đặt `.env.local` của web theo outputs: `VITE_API_URL`, `VITE_AWS_REGION`, `VITE_COGNITO_USER_POOL_ID`, `VITE_COGNITO_CLIENT_ID`, `VITE_ENABLE_MOCK_DATA=false`, `VITE_ENABLE_MOCK_USER=false`. Chạy `pnpm build` rồi `pnpm preview --port 5173 --strictPort`. `pnpm dev` luôn cho phép fallback dữ liệu mẫu nên không dùng nó để kết luận API AWS đã chạy.

Đăng nhập hai tenant, kiểm tra `/me`, tải CSV, duyệt SKU và đọc dữ liệu riêng. Ghi mã HTTP và tenant thực nhận khi thử job/product/report của tenant khác, Viewer ghi dữ liệu, user bị vô hiệu hóa và mời lại cùng `sub`. Không lưu JWT/presigned URL còn hiệu lực vào ảnh chụp hoặc Git.

## 7. Kênh nhận báo cáo theo tenant

Main stack xuất `NotificationKeyArn`. Mỗi tenant có một stack riêng theo `infra/aws/tenant-notifications.yaml`, nhận các tham số `MainStackName`, `TenantId`, `Email` và `NotificationKeyArn`. Topic SNS và secret Slack được đặt trong namespace của main stack và tenant; không dùng chung một topic mặc định cho nhiều doanh nghiệp.

Nếu thêm email sau khi tạo topic, dùng `scripts.subscribe_tenant_email` với `--stack marketdata-hoang-dev`, `--tenant-id`, `--topic-arn` và `--email` của tenant. Helper kiểm tra đúng topic `${stack}-tenant-${tenant_id}` và gắn filter `tenant_id`; thao tác subscribe sẽ gửi email xác nhận nên chỉ chạy khi đã có người nhận đồng ý.

Sau khi tạo tenant stack, lấy outputs `ReportTopicArn` và `SlackSecretArn`. Điền secret Slack JSON `{"webhook_url":"..."}` qua Console trước khi gắn ARN cho tenant; secret mới tạo chưa có giá trị. Sau đó lưu vào META `sns_topic_arn` và `slack_secret_arn` của đúng tenant bằng cập nhật có điều kiện. Nếu không dùng Slack thì chưa gắn `slack_secret_arn`. Không lưu webhook trong META hoặc `.env`.

Email phải xác nhận subscription. Khi kiểm thử, ghi rõ SNS Publish đã được chấp nhận hay email đã thực sự đến hộp thư; đó là hai kết quả khác nhau. Các khóa KMS, SNS, CloudWatch, PITR, S3 và artifact SAM có thể phát sinh chi phí; kiểm tra Billing và chỉ bật những kênh nhóm đang dùng.

## 8. Bằng chứng và dọn môi trường

`build/evidence` bị Git ignore. Giữ bản JSON và ảnh chụp đã bỏ credential/token cho báo cáo tổng kết. Ghi commit local, ngày chạy, stack/Region và phân biệt kiểm thử Moto với AWS thật. Không điền “đạt” cho một mục chưa chạy hoặc chỉ dựa vào template.

Smoke test giữ fixture để kiểm tra: hai partition `T#smoke-a-...`, `T#smoke-b-...`, các partition quan sát sản phẩm của chúng, và phiên bản S3 có tên trong JSON. Sau khi lưu bằng chứng, chỉ xóa dữ liệu của các tenant fixture đó. Không làm trống bucket/bảng, không xóa dữ liệu pilot hoặc bảng dùng chung.

Bucket, bảng và khóa KMS có Retain; xóa stack không tự xóa những tài nguyên này. Kiểm kê từng stack và tài nguyên đã giữ, sao lưu dữ liệu cần thiết rồi mới dọn. Khóa KMS còn được tenant stack sử dụng không được lên lịch xóa. Không bật lịch tự động để thử nếu các tenant chưa cấu hình đầy đủ nguồn hoặc kênh nhận.

Tham khảo: [CloudFormation Retain](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-attribute-deletionpolicy.html), [SQS/Lambda](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html), [S3 EventBridge](https://docs.aws.amazon.com/AmazonS3/latest/userguide/enable-event-notifications-eventbridge.html), [SAM deploy](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/using-sam-cli-deploy.html).
