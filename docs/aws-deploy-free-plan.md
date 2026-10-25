# Triển khai phần Hoàng bằng tài khoản AWS cá nhân Free plan

Hướng dẫn cho Windows/PowerShell, dùng bản code local trong `DataMarket-Pipeline`. Chưa có lệnh triển khai nào được chạy thay bạn. Không cần push GitHub để triển khai từ máy cá nhân.

## 1. Chọn tài khoản và theo dõi credit

Nếu môn học không bắt buộc dùng AWS Academy, dùng tài khoản cá nhân đang ở Free plan cho lần này. Template cần tạo IAM role riêng và nhiều dịch vụ; quyền của tài khoản Academy phụ thuộc cấu hình lab của trường, chưa được kiểm tra.

Free plan có thời hạn tối đa 6 tháng hoặc đến khi hết credit, tùy điều kiện nào đến trước. Khi đó tài khoản có thể đóng và bạn mất quyền truy cập tài nguyên. Theo AWS, Free plan không phát sinh khoản phải trả khi vẫn ở gói này; Paid plan tính tiền cho sử dụng vượt credit hoặc dịch vụ không áp dụng credit. Kiểm tra tên plan, credit thực có và ngày hết hạn trong Billing; không coi “tối đa 200 USD” là số credit chắc chắn đang còn.

Trước khi triển khai:

1. Vào Billing and Cost Management, kiểm tra Free plan và số credit còn lại.
2. Tạo cost budget thử nghiệm 10 USD/tháng, email cảnh báo khi đạt 50% và 100%. Đây là ngưỡng tự chọn để theo dõi, không phải ước tính chi phí của dự án hoặc cơ chế tự tắt tài nguyên. Nếu cần theo dõi chi phí trước khi trừ credit, bỏ tính credit trong phần cost types của budget.
3. Giữ ScheduleState=DISABLED. Chưa chạy crawler, AI hoặc thông báo tự động.
4. Nếu có màn hình yêu cầu nâng Paid plan hoặc kích hoạt tính năng trả phí, dừng tại đó và kiểm tra dịch vụ nào bị hạn chế. Không nâng gói chỉ để bỏ qua lỗi triển khai.

Các dịch vụ AWS chính trong thiết kế có tên trong danh sách hỗ trợ Free Tier hiện tại, nhưng quyền, quota và từng tính năng vẫn phải kiểm tra trên tài khoản của bạn. Không có bảo đảm rằng 200 USD sẽ đủ cho mọi cách chạy. Giai đoạn đầu chỉ thử fixture nhỏ và xem chi phí thực tế.

## 2. Cài công cụ

Cài bằng bộ cài chính thức, sau đó mở PowerShell mới:

- [AWS CLI v2 cho Windows](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html).
- [AWS SAM CLI cho Windows](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html).
- Python 3.12 nếu máy chưa có Python 3.12 thông thường. Dự án đã có .venv trên máy hiện tại.

```powershell
aws --version
sam --version
Set-Location 'C:\Users\Phuc Minh Hoang\Downloads\Idea\DataMarket-Pipeline'
.venv\Scripts\python.exe --version
```

AWS CLI cần từ 2.32.0 để dùng cách đăng nhập trình duyệt dưới đây. Nếu máy không nhận aws/sam, mở terminal mới và kiểm tra PATH trước khi làm tiếp.

## 3. Đăng nhập và xác nhận đúng tài khoản

Dùng danh tính IAM có quyền triển khai stack này. Với IAM user/role, cách login trình duyệt cần quyền SignInLocalDevelopmentAccess; quyền đó chỉ cho phép đăng nhập local, không tự cấp quyền tạo tài nguyên. Không tạo access key cho root và không gửi password/token/key trong chat.

```powershell
aws login --profile marketdata-login
```

Chọn tài khoản cá nhân Free trong trình duyệt, Region ví dụ `us-east-1`. Giữ một Region xuyên suốt; có thể dùng Region khác nếu nhóm đã chốt và các dịch vụ hỗ trợ.

Tạo profile tương thích cho SAM và Python SDK. Profile này lấy credential tạm thời từ AWS CLI, không chứa secret trong repo:

```powershell
aws configure set credential_process 'aws configure export-credentials --profile marketdata-login --format process' --profile marketdata-dev
aws configure set region us-east-1 --profile marketdata-dev
aws sts get-caller-identity --profile marketdata-dev
```

Đối chiếu Account với tài khoản cá nhân trong Console. Nếu hiện tài khoản Academy thì đăng nhập lại đúng tài khoản trước khi deploy. Chỉ chạy `export-credentials` qua credential_process, không in credential ra màn hình hoặc chép vào file dự án.

Danh tính triển khai cần quyền CloudFormation/SAM, tạo và PassRole cho IAM, cùng các tài nguyên trong template: S3, Lambda, DynamoDB, SQS, EventBridge/Scheduler, CloudWatch, Cognito, API Gateway và CloudFront. Nếu bị AccessDenied, đọc đầy đủ nguyên nhân: chỉ bổ sung quyền khi lỗi xác định action/resource bị thiếu quyền. Riêng thông báo CloudFront yêu cầu xác minh tài khoản phải xử lý với AWS Support; thêm quyền IAM không giải quyết yêu cầu xác minh. Không sửa worker để bỏ kiểm tra tenant hoặc mở bucket công khai.

## 4. Chuẩn bị code và kiểm tra

```powershell
Set-Location 'C:\Users\Phuc Minh Hoang\Downloads\Idea\DataMarket-Pipeline'
.venv\Scripts\python.exe -m pip install -r requirements-dev.lock.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m scripts.stage_lambda
.venv\Scripts\python.exe -m scripts.validate_template
.venv\Scripts\cfn-lint.exe infra/aws/template.yaml build/cloudformation.json
```

Để SAM tìm đúng Python 3.12 trong phiên PowerShell này:

```powershell
$projectPython = (Resolve-Path '.venv\Scripts').Path
$env:PATH = "$projectPython;$env:PATH"
sam build --template-file infra/aws/template.yaml
```

Kết quả cần có `Build Succeeded` và `.aws-sam/build/template.yaml`. Không dùng `build/cloudformation.json` để deploy: file đó chỉ để kiểm tra offline, chứa vị trí artifact giả. SAM build/package sẽ tạo artifact thật.

Nếu báo PythonPipBuilder không tìm được Python 3.12, kiểm tra Python trong PATH. Nếu không build được trên Windows, có thể dùng Docker Desktop và `sam build --use-container --template-file infra/aws/template.yaml`; không cần Docker chỉ để chạy bộ pytest/Moto.

## 5. Tạo stack

Lệnh này mới là bước tạo tài nguyên AWS thật:

```powershell
sam deploy --guided --profile marketdata-dev --region us-east-1 --stack-name marketdata-hoang-dev --capabilities CAPABILITY_IAM --parameter-overrides WebOrigin=http://localhost:5173 ScheduleState=DISABLED EnableCloudFront=DISABLED
```

Các lựa chọn trong guided:

| Câu hỏi | Chọn |
| --- | --- |
| Stack Name | marketdata-hoang-dev |
| AWS Region | us-east-1, hoặc Region nhóm đã thống nhất |
| WebOrigin | http://localhost:5173 trong giai đoạn này |
| ScheduleState | DISABLED |
| EnableCloudFront | DISABLED (chờ AWS Support duyệt CloudFront xong mới bật ENABLED) |
| Confirm changes before deploy | Y |
| Allow SAM CLI IAM role creation | Y, nếu danh tính của bạn được cấp quyền tạo role |
| Disable rollback | N |
| Save arguments to configuration file | N cho lần đầu để tránh dùng nhầm profile/Region ở lần sau |

Đọc change set, xác nhận đây là stack dev mới của mình rồi mới chọn triển khai. Chờ CREATE_COMPLETE; CloudFront có thể làm bước này chậm hơn các dịch vụ còn lại. SAM sẽ tạo bucket lưu artifact nếu cần.

Nếu CloudFormation báo ROLLBACK_COMPLETE, xem tab Events và tìm các lỗi CREATE_FAILED có nguyên nhân cụ thể; không bấm deploy lặp lại liên tục. Ghi nhận tài nguyên Retain đã tạo trước khi dọn stack lỗi. Không xóa tài nguyên của stack khác.

Kiểm tra tất cả CREATE_FAILED có nguyên nhân cụ thể vì nhiều tài nguyên được tạo song song. Các dòng chỉ ghi `Resource creation cancelled` là bị hủy theo, chưa chứng minh bản thân tài nguyên đó cấu hình đúng hoặc sai. Nếu gặp lỗi xác minh CloudFront như mục 8.1, dừng ở đây; chưa chạy phần smoke test.

## 6. Kiểm tra outputs và tài nguyên

```powershell
aws cloudformation describe-stacks --stack-name marketdata-hoang-dev --profile marketdata-dev --region us-east-1 --query 'Stacks[0].Outputs' --output table
```

Trong Console, cùng Region, kiểm tra:

- Lambda: Normalizer, CSVWorker, JobDispatcher, ReportDispatcher.
- SQS: năm queue chính và DLQ riêng.
- DynamoDB: ControlTable, ObservationsTable và index by_day.
- S3: RawBucket, ReportsBucket, WebBucket, đều chặn public access.
- Scheduler: hai lịch ở trạng thái DISABLED.
- CloudWatch: log group và alarm DLQ.

WebUrl chưa có giao diện đến khi Minh tải web build lên S3; HTTP API chưa có route nghiệp vụ đến khi Hiếu tích hợp. CloudFront báo 403 lúc bucket web chưa có index.html không đồng nghĩa luồng dữ liệu bị lỗi. Không mở public bucket để sửa lỗi này.

## 7. Thử CSV thật khi chưa có API

Đây là thao tác seed bởi người quản trị trong sandbox, không phải luồng cho người dùng cuối. Nó tạo hai tenant mới dành riêng cho kiểm thử và tải file vào RawBucket. Cần giữ nguyên profile và Region đã xác nhận. Đợi EventBridge notification có hiệu lực nếu vừa tạo cấu hình S3; [AWS ghi nhận việc bật có thể mất khoảng 5 phút](https://docs.aws.amazon.com/AmazonS3/latest/userguide/enable-event-notifications-eventbridge.html).

Trong PowerShell, từ thư mục gốc, đặt profile cho tiến trình Python rồi chạy đoạn sau. Không dùng `scripts.demo` để chứng minh AWS thật vì demo luôn chạy qua Moto.

```powershell
$env:AWS_PROFILE = 'marketdata-dev'
$env:AWS_DEFAULT_REGION = 'us-east-1'
@'
import csv
import io
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
from marketdata.repository import DynamoStore

session = boto3.Session()
cfn = session.client('cloudformation')
stack = cfn.describe_stacks(StackName='marketdata-hoang-dev')['Stacks'][0]
assert stack['StackStatus'] in ('CREATE_COMPLETE', 'UPDATE_COMPLETE')
outputs = {item['OutputKey']: item['OutputValue'] for item in stack['Outputs']}
store = DynamoStore(outputs['ControlTable'], outputs['ObservationsTable'],
                   resource=session.resource('dynamodb'), client=session.client('dynamodb'))
suffix = uuid.uuid4().hex[:8]
tenant_a, tenant_b = 'smoke-a-' + suffix, 'smoke-b-' + suffix
job_id = 'csv-' + suffix

for tenant in (tenant_a, tenant_b):
    store.put(tenant, 'META', {'status': 'active', 'product_count': 0})
    store.put(tenant, 'SOURCE#vendor', {'kind': 'csv', 'enabled': True})
    store.create_product(tenant, 'phone', 'Phone smoke test')
    store.set_mapping(tenant, 'vendor', '001-PHONE', 'phone')

key = f'tenants/{tenant_a}/raw/{job_id}'
store.create_job(tenant_a, job_id, {'source_id': 'vendor', 'kind': 'csv',
                                  'status': 'queued', 'raw_key': key})
fixture = Path('tests/fixtures/prices.csv')
rows = list(csv.DictReader(fixture.open(encoding='utf-8', newline='')))
observed = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
for row in rows:
    row['observed_at'] = observed
buffer = io.StringIO(newline='')
writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
writer.writeheader()
writer.writerows(rows)
uploaded = session.client('s3').put_object(Bucket=outputs['RawBucket'], Key=key,
                               Body=buffer.getvalue().encode('utf-8'), ContentType='text/csv')
retry_event = {'source': 'aws.s3', 'detail-type': 'Object Created', 'detail': {
    'bucket': {'name': outputs['RawBucket']},
    'object': {'key': key, 'version-id': uploaded['VersionId']}}}
print('CSV queue URL:', outputs['CSVQueueUrl'])
print('Retry event JSON:', json.dumps(retry_event))
print('Testing tenant:', tenant_a, 'job:', job_id)
deadline = time.monotonic() + 360
while time.monotonic() < deadline:
    job = store.get(tenant_a, 'JOB#' + job_id)
    if job.get('status') == 'failed':
        raise RuntimeError('CSV failed: ' + job.get('error', 'check CloudWatch'))
    if job.get('status') == 'completed':
        break
    time.sleep(5)
else:
    raise TimeoutError('Check CSVWorker/Normalizer logs, EventBridge, queues and DLQs')

assert job['counts'] == {'accepted': 90, 'pending': 0, 'rejected': 10}, job['counts']
assert len(list(store.product_history(tenant_a, 'phone'))) == 90
assert list(store.product_history(tenant_b, 'phone')) == []
print(json.dumps({'tenant_a': tenant_a, 'tenant_b': tenant_b,
                  'counts': job['counts']}, default=str, indent=2))
'@ | .venv\Scripts\python.exe -
```

Kết quả mong đợi: 90 accepted, 10 rejected, 0 pending; tenant B có 0 quan sát. Kiểm tra này xác nhận luồng hạ tầng và repository, chưa chứng minh JWT/RBAC qua HTTP vì API chưa có route.

Muốn thử retry, dùng SQS Console mở queue theo CSV queue URL đã in, chọn Send message và dán Retry event JSON đã in ở trên. Giữ nguyên tenant/job/key/version-id. Không PUT đè file để giả lập retry: worker cố ý chặn phiên bản khác của cùng job. Sau retry số quan sát vẫn phải là 90.

## 8. Lỗi thường gặp và bước tiếp theo

| Lỗi | Cách xử lý |
| --- | --- |
| Unable to locate credentials / session expired | Chạy lại aws login cho marketdata-login; kiểm tra get-caller-identity bằng marketdata-dev. |
| AccessDenied IAM/CreateRole/PassRole | Kiểm tra quyền danh tính triển khai; không bỏ giới hạn bảo mật trong code để né lỗi. |
| AWS yêu cầu nâng Paid | Dừng và xác định tính năng bị chặn, chưa nâng gói. |
| CloudFront: Your account must be verified | Mở yêu cầu xác minh tài khoản với AWS Support theo mục 8.1; không tự thêm AdministratorAccess hoặc nâng Paid để thử sửa. |
| HttpApi: Invalid API name specified | Kiểm tra Name trong infra/aws/template.yaml, rồi sam build lại; bản local hiện đã có Name. |
| HttpApi: / Properties: is not recognized | Đây là YAML nhập nhầm vào PowerShell. Chỉnh YAML trong file template, không chạy các dòng đó như lệnh. |
| CSV chưa xử lý | Kiểm tra EventBridge notification, rule target/queue policy, CSVQueue và log CSVWorker. |
| Có TransformQueue message nhưng job chưa xong | Kiểm tra Normalizer, event source mapping, IAM và log; xem receipt mã lỗi. |
| Web 403 / API 404 | Chưa có web build hoặc route nghiệp vụ; chờ phần Minh/Hiếu tích hợp. |
| Dữ liệu mẫu quá hạn | Đoạn smoke test ở trên tự đổi observed_at theo giờ chạy; không upload fixture ngày cũ trực tiếp. |

Bàn giao outputs cho nhóm, không bàn giao access key. Chỉ bật hai lịch khi có consumer API/HTML và Aggregator tương ứng. Sau đó mới nối web, Cognito/JWT, API và thử quyền qua HTTP với hai tenant thật.

Khi dọn thử nghiệm, kiểm tra stack đúng của mình rồi dùng `sam delete --stack-name marketdata-hoang-dev --profile marketdata-dev --region us-east-1`. Bucket và table có Retain nên vẫn cần kiểm tra/xóa riêng khi đã sao lưu và chắc chắn không còn dùng. Xóa stack không đảm bảo mọi tài nguyên còn giữ và bucket artifact đều đã hết chi phí.

### 8.1. Phân tích log triển khai đã gửi

Kết luận này dựa trên log PowerShell đã gửi, không phải truy vấn trạng thái AWS hiện tại. Log có hai lần tạo stack, với hai lỗi gốc khác nhau:

| Lần / tài nguyên | Bằng chứng | Nguyên nhân và trạng thái |
| --- | --- | --- |
| Lần 1: HttpApi, AWS::ApiGatewayV2::Api | CREATE_FAILED, Invalid API name specified, HTTP 400 | Template trước thiếu Name. Bản local đã bổ sung tên theo stack; lần 2 HttpApi đạt CREATE_COMPLETE, nên lỗi này đã được xử lý trong lần chạy đó. |
| Lần 2: WebDistribution, AWS::CloudFront::Distribution | CREATE_FAILED, Your account must be verified before you can add new CloudFront resources, HTTP 403 | AWS yêu cầu xác minh tài khoản cho CloudFront. Đây là điểm chặn còn lại trong log; không phải bằng chứng thiếu một quyền IAM cụ thể. |

Các lỗi `HttpApi:`, `Type:`, `Properties:` không nhận diện lệnh xuất hiện khi nhập YAML vào PowerShell. Nội dung đó thuộc file `infra/aws/template.yaml`. Việc mở Notepad chỉnh file rồi build lại là đúng. Không cần đổi loại HttpApi sang AWS::Serverless::HttpApi để xử lý lỗi này.

`41 passed` và `Build Succeeded` xác nhận kiểm thử local và đóng gói đã qua trong lần chạy được ghi lại. Chúng không kiểm tra điều kiện xác minh tài khoản CloudFront. Thông báo `File with same data already exists ... skipping upload` chỉ cho biết SAM tái sử dụng artifact có cùng nội dung.

Sau lỗi CloudFront, nhiều queue, bucket policy và tài nguyên khác ghi `Resource creation cancelled`. Stack kết thúc ở `ROLLBACK_COMPLETE`; phần Lambda chưa được chứng minh hoạt động trên AWS qua log này. Thông báo `Rollback requested by user` phù hợp với chế độ rollback đã bật, không nhất thiết là bạn vừa bấm hủy thủ công.

**Bước tiếp theo cho tài khoản:** mở [AWS Support Center](https://console.aws.amazon.com/support/home#/), tạo yêu cầu về tài khoản/xác minh và đính kèm lỗi CloudFront. [Basic Support có hỗ trợ vấn đề tài khoản và thanh toán](https://docs.aws.amazon.com/awssupport/latest/user/aws-support-plans.html); không cần mua gói hỗ trợ kỹ thuật chỉ để hỏi về xác minh tài khoản. AWS sẽ xác định yêu cầu xác minh cụ thể; không có thời gian xử lý hoặc kết quả phê duyệt được bảo đảm.

Nội dung tham khảo để tự gửi, không chứa credential:

```text
Subject: Account verification required to create a CloudFront distribution

I am deploying a small university Cloud Computing project using AWS SAM.
CloudFront distribution creation failed with:
"Your account must be verified before you can add new CloudFront resources."

CloudFormation stack: marketdata-hoang-dev
Stack region: us-east-1
CloudFront request ID: 6bb7db3b-58e3-4719-970f-652ffddc4fd6

Please review my account's eligibility to create CloudFront distributions
and let me know which account verification steps are required.
I would like to remain on my current Free account plan.
```

Trong khi chờ, giữ ScheduleState=DISABLED. Chạy lại cùng template, đổi Region, tăng quyền IAM hoặc nâng Paid không phải cách khắc phục đã được chứng minh cho lỗi xác minh này. Tạm triển khai riêng phần dữ liệu là một phương án phát triển khác, nhưng cần sửa template để có tùy chọn CloudFront cùng các tham chiếu liên quan; template hiện tại chưa có tùy chọn đó. Không xóa riêng một khối WebDistribution hoặc mở public S3 để bỏ qua lỗi.

**Kiểm kê trước khi tạo lại stack:** log lần 2 có DELETE_SKIPPED cho ControlTable, ObservationsTable, RawBucket, ReportsBucket và WebBucket do chính sách Retain. Không kết luận chúng đã bị xóa sau rollback. Lần tạo đầu tiên cũng có thể để lại tài nguyên; cần đối chiếu riêng trong Console trước khi dọn hoặc tạo thêm.

Các lệnh sau chỉ đọc trạng thái và ID tài nguyên, không xóa hoặc tạo tài nguyên:

```powershell
aws cloudformation describe-stacks --stack-name marketdata-hoang-dev --profile marketdata-dev --region us-east-1 --query 'Stacks[0].{Status:StackStatus,StackId:StackId}' --output table
aws cloudformation list-stack-resources --stack-name marketdata-hoang-dev --profile marketdata-dev --region us-east-1 --query 'StackResourceSummaries[?ResourceStatus==`DELETE_SKIPPED`].{LogicalId:LogicalResourceId,PhysicalId:PhysicalResourceId,Type:ResourceType,Status:ResourceStatus}' --output table
```

Ghi lại PhysicalId và kiểm tra dữ liệu hiện có. Nếu stack đã bị xóa sau khi gửi log, cần dùng StackId/ARN của lần chạy cũ cho việc tra cứu lịch sử; không đoán rằng tên stack hiện tại trỏ tới lần tạo cũ.

Theo [trạng thái CloudFormation](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/view-stack-events.html), ROLLBACK_COMPLETE không thể cập nhật như một stack đã tạo thành công. Sau khi xử lý điểm chặn CloudFront, cần quyết định cách giữ/nhập lại tài nguyên cũ hoặc tạo lại môi trường dev trước khi triển khai tiếp. Không xóa stack hoặc bucket/table theo một lệnh dọn chung. [Tài nguyên Retain tiếp tục tồn tại và có thể có chi phí](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-attribute-deletionpolicy.html); stack mới không tự nhận lại những tài nguyên đó.

## Tài liệu AWS đối chiếu

- [Free và Paid account plans](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html).
- [Dịch vụ hỗ trợ trong trải nghiệm Free Tier mới](https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html), có thể khác tùy trải nghiệm tài khoản.
- [AWS CLI login và credential_process](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html).
- [SAM deploy guided](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/using-sam-cli-deploy.html).
- [AWS Budgets](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-create.html).
