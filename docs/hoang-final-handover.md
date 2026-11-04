# Bàn giao phần việc của Vũ Minh Hoàng

Nhóm 07, học phần Điện toán đám mây, dự án MarketData Pipeline. Bản hợp nhất ngày 04/11/2026 nhận `origin/main` tại commit `39c18f1`, trên nhánh `codex/hoang-final-integration`. Các kết quả AWS ngày 01/11 bên dưới thuộc phiên bản trước khi hợp nhất, dựa trên commit `e67e066` và phần sửa local của Hoàng.

## Hợp nhất ngày 04/11/2026

- Giữ NotificationFunction riêng và retry từng kênh của nhóm; kết hợp kiểm tra ARN theo tenant và Slack secret của Hoàng. Aggregator gọi bất đồng bộ hàm này sau khi lưu báo cáo.
- Dùng `AnthropicSecretArn` cho template; không đưa lại API key vào tham số deploy. Quyền Claude thuộc Aggregator, quyền SNS/Slack/KMS thuộc NotificationFunction.
- Giữ report lock và ID quan sát theo nội dung từ GitHub; receipt vẫn có ID theo job/dòng để mỗi job có bộ đếm riêng. Giữ transaction membership, quota và điều kiện bảo vệ catalog của Hoàng.
- Nhận cấu hình URL/header nguồn qua UI/API và kiểm tra đích HTTP công khai. API che giá trị header nhạy cảm khi trả cho người dùng không có quyền Admin.
- Bỏ global search, nút thông báo và trạng thái sức khỏe tĩnh chưa có chức năng ở khung giao diện. Giữ tìm kiếm đang hoạt động trong Catalog/Dashboard.

Bản hợp nhất chưa được triển khai lại lên AWS. Không dùng kết quả smoke ngày 01/11 để xác nhận NotificationFunction mới, chống trùng giữa nhiều job hoặc UI nguồn đã chạy trên môi trường thật. Script migration observation ID có chế độ dry-run; không chạy migration cùng thao tác đồng bộ Git.

Kiểm tra ngày 04/11: 239 kiểm thử Python đã đạt. Lần chạy trong sandbox có 5 test HTTP bị Windows chặn socket (WinError 10013); chạy lại toàn bộ nhóm HTTP ngoài sandbox đạt 35/35. Ruff, pip check và cfn-lint đạt; SAM dịch offline được 101 tài nguyên. Frontend đạt 34/34 Vitest, 3/3 test Node cho công cụ smoke và production build với cả hai cờ mock tắt. Bundle JavaScript còn khoảng 2.373 kB minified; chưa tối ưu chia chunk và vẫn có cảnh báo deprecation Antd trong Catalog.

## Đối chiếu Báo cáo tiến độ 2

44 giờ trong kế hoạch còn lại là ước tính đã nộp, không phải số giờ thực tế đã thực hiện. Bảng này chỉ ghi phần Hoàng phụ trách hoặc phối hợp, không nhận thay kết quả của cả nhóm.

| Mã | Phần việc của Hoàng | Sản phẩm bàn giao |
| --- | --- | --- |
| A1–A2 | Hợp nhất bản sửa hạ tầng, kiểm kê stack và kiểm thử CSV | Template có tên HTTP API, CloudFront theo điều kiện, script `verify_aws` và `smoke_test_aws` |
| A3 | IAM, SNS theo tenant, Secrets Manager và cảnh báo DLQ | Template chính và `tenant-notifications.yaml`; bộ đọc secret; kiểm tra namespace đích gửi |
| A4 | Phối hợp bảo vệ membership | Transaction ghi hai bản ghi và phiên bản tenant; chặn ghi đè Cognito sub sang tenant khác; kiểm tra quota đồng thời |
| A7 | Hạ tầng cho worker API/web | Consumer, IAM, hàng đợi/DLQ và công cụ kiểm tra AWS; đã nhận phần cấu hình URL nguồn qua UI/API của nhóm, còn kiểm thử nguồn thật sau triển khai |
| A8 | Chuẩn hóa/catalog và cách ly tenant | Kiểm tra Decimal, UTC, SKU, chống trùng; transaction duyệt không chuyển cùng mã nguồn sang hai sản phẩm khác nhau |
| A13 | Cấu hình phân phối web và CORS | CloudFront không được tạo khi tắt; WebOrigin cho frontend local hoặc nhà cung cấp khác; không mở công khai bucket dữ liệu |
| A15 | Kiểm tra quyền và hai tenant | Kiểm thử giả lập; smoke API dùng Cognito SRP/JWT thật, kiểm tra Viewer, thu hồi quyền và truy cập chéo |
| A16 | Retry, partial batch, DLQ và log | Smoke replay cùng phiên bản CSV, phát lại đủ 100 dòng; tùy chọn theo dõi một message lỗi có marker riêng vào DLQ |
| A20 | Sửa lỗi tích hợp trong phạm vi trên | Chặn ký URL báo cáo ngoài tenant; hoàn thiện dependency/CI và bằng chứng kiểm thử |

## Những thay đổi cần giải thích khi bảo vệ

- Hạ tầng dùng chung nhưng mỗi thao tác dữ liệu phải có tenant. IAM giới hạn tài nguyên của Lambda; kiểm tra membership và khóa dữ liệu ở ứng dụng quyết định tenant nào được truy cập.
- `save_member` ghi `U#<sub>/MEMBERSHIP`, `T#<tenant>/MEMBER#<sub>` và cập nhật `META.membership_version` trong một transaction. Thao tác đồng thời phải đọc lại phiên bản và quota, không ghi hai bản độc lập.
- Nếu Cognito tạo user thành công nhưng DynamoDB lỗi, user chưa có quyền ứng dụng. Lần mời lại lấy cùng Cognito sub rồi thử transaction; không tự xóa một user có thể đã tồn tại.
- Receipt giữ mã theo tenant/source/job/dòng để đếm từng job. Observation dùng content ID để dữ liệu cùng nội dung qua job mới không tạo thêm quan sát. Cùng mã SKU nguồn đã được duyệt không thể đồng thời ánh xạ sang hai sản phẩm khác nhau.
- ReportQueue có visibility timeout 720 giây cho Lambda tổng hợp timeout 120 giây. Các worker dữ liệu dùng 300 giây cho timeout 45 giây; partial batch chỉ trả lại message lỗi.
- SNS báo cáo và Slack secret thuộc namespace từng tenant. Runtime xác minh toàn bộ ARN trước khi gửi. Không dùng SNS hoặc webhook chung làm kênh dự phòng giữa các tenant.
- Khóa Claude đọc từ secret JSON có trường `api_key`; Slack dùng `webhook_url`. Tenant metadata chỉ giữ ARN. Giá trị secret được cache tối đa 5 phút và không xuất vào log.
- CloudFront đã bị AWS từ chối cho tài khoản hiện tại. Giữ `EnableCloudFront=DISABLED`; việc chạy giao diện cục bộ không được ghi thành đã phân phối web công khai trên AWS.

## Kiểm tra và bằng chứng

Kiểm tra cục bộ ngày 01/11/2026: 129 kiểm thử Python đạt; 3 kiểm thử Node cho công cụ smoke API và 27 kiểm thử Vitest đạt; Ruff và pip check đạt. SAM build dùng Python 3.12 và frontend production build thành công. Frontend còn cảnh báo bundle JavaScript lớn, chưa có số đo thời gian tải trên trình duyệt. cfn-lint đạt cho hai template và bản dịch 96 tài nguyên. Demo CSV hai lần vẫn có 90 accepted/10 rejected; demo API/web có 6 quan sát ở tenant A, tenant B có 0. Đây là kết quả giả lập, tách khỏi các lần chạy AWS bên dưới.

Kiểm kê AWS ban đầu ngày 01/11/2026: stack `marketdata-hoang-dev` ở `CREATE_COMPLETE`, CloudFront và Scheduler tắt, nhưng còn thiếu API/Web worker, ApiFunction, ReportAggregator và AlarmActions. Lần cập nhật đầu rollback vì SNS không chấp nhận `sns:*` trong topic policy. Đã thay bằng `sns:Publish` và `sns:Subscribe` ở cả hai template, bổ sung kiểm thử hồi quy và chạy lại 9 kiểm thử hạ tầng cùng cfn-lint, đều đạt.

Change set lần hai `samcli-deploy1793544719` có 42 tài nguyên thêm và 18 thay đổi; chỉ thay thế 6 alarm, không thay đổi bucket, bảng dữ liệu hoặc Cognito. Đã áp dụng sau khi người phụ trách cho phép. Stack đạt `UPDATE_COMPLETE` trong Region `us-east-1` ngày 01/11/2026; CloudFront và hai lịch tự động vẫn tắt, WebOrigin là `http://localhost:5173`.

| Kiểm thử AWS thật | Kết quả ghi nhận ngày 01/11/2026 |
| --- | --- |
| Kiểm kê cấu hình | 56/56 mục đạt; đủ 5 SQS consumer, partial batch, mã hóa, bucket riêng tư và 6 alarm có đích SNS |
| CSV qua S3 → EventBridge → SQS → worker → Normalizer | 100 dòng: 90 accepted, 10 rejected, 0 pending; tenant A có 90 quan sát, tenant B có 0 |
| Phát lại CSV và Normalizer | Cùng phiên bản S3 được phát lại; đưa lại đủ 100 dòng tới Normalizer trên AWS; số quan sát và bộ đếm không đổi |
| Cognito SRP và HTTP API | 15/15 kiểm tra đạt: xác định tenant/vai trò, JWT thiếu/sai, Viewer ghi dữ liệu, truy cập chéo job/report/receipt, tenant trong body và thu hồi quyền |
| CORS bằng HTTP OPTIONS | 4/4 kiểm tra đạt: API và S3 Raw cho phép `http://localhost:5173`; origin khác không nhận quyền CORS. Chưa thay thế kiểm thử trên trình duyệt |
| Dọn dữ liệu API thử | Đã xóa đúng 3 user Cognito, 2 partition tenant thử và membership của lần chạy API; không gửi email mời |
| Message lỗi vào TransformQueue DLQ | Đạt lúc 22:24:20 giờ Việt Nam: đúng marker và MessageId đã xuất hiện trong DLQ. CloudWatch ghi 5 lượt Normalizer xử lý lỗi, cách nhau khoảng 300 giây; không đổi cấu hình queue hoặc đưa message thẳng vào DLQ |
| Alarm DLQ và SNS vận hành | Alarm tự chuyển sang ALARM lúc 22:27:41; lịch sử CloudWatch ghi `Successfully executed action` tới AlarmTopic. Không có email subscription được cấu hình trong lần chạy này, chưa xác nhận gửi email báo cáo |

Kết quả chỉ được ghi đạt sau khi lệnh tương ứng kết thúc thành công. Các file trong `build/evidence/` là đầu ra cục bộ, bị gitignore.

| Kiểm tra | Bằng chứng |
| --- | --- |
| Tổng hợp kết quả AWS | `build/evidence/final-summary.json` |
| Python unit/integration bằng Moto | `build/evidence/pytest.xml` |
| Cập nhật stack | `build/evidence/deployment-complete.json`, `build/evidence/change-set-retry.json` |
| Cấu hình AWS trước cập nhật | `build/evidence/aws-preflight-before.json` |
| Cấu hình AWS sau cập nhật | `build/evidence/aws-preflight.json` |
| CSV, retry và DLQ trên AWS | `build/evidence/csv-dlq-smoke.json` |
| Các lượt Lambda xử lý message lỗi | `build/evidence/dlq-retry-log.json` |
| Alarm, metric và hành động SNS | `build/evidence/dlq-alarm-state.json`, `build/evidence/dlq-metric.json`, `build/evidence/dlq-alarm-action-history.json` |
| Đăng nhập và quyền API trên AWS | `build/evidence/aws-api-smoke.json` |
| Preflight CORS HTTP thật, không ghi dữ liệu | `build/evidence/aws-cors.json` |
| Lần rollback và KMS key giữ lại | `build/evidence/deploy-attempt-1-all-events.json`, `build/evidence/retained-key-after-rollback.json` |

Chưa có kết quả Guard: `cfn-guard` không nằm trong yêu cầu báo cáo/CI hiện tại và người phụ trách chọn không cài thêm. cfn-lint kiểm tra cấu trúc template; regression test kiểm tra những quy tắc của dự án, không thay thế đánh giá compliance đầy đủ. CloudTrail Event History có ghi thao tác CloudFormation nhưng tài khoản chưa có trail lưu trữ dài hạn tại lần kiểm kê.

`receive_count` đọc tại DLQ là số lần nhận do SQS báo, không dùng để suy ra số lần Lambda đã retry. Bằng chứng về 5 lượt xử lý nằm trong log có đúng MessageId. Message được giữ ở DLQ để đối chiếu, không dùng lệnh purge hoặc xóa message khác.

## Nội dung có thể dùng cho báo cáo tổng kết

Phần hạ tầng và chuẩn hóa dữ liệu đã được triển khai trên AWS bằng SAM, tại Region us-east-1. Kiểm thử CSV 100 dòng cho kết quả 90 dòng hợp lệ, 10 dòng bị loại; phát lại không làm tăng số quan sát và tenant thử thứ hai không nhận dữ liệu. Kiểm thử bằng Cognito SRP và HTTP API thật đạt 15 trường hợp về xác thực, phân quyền và cách ly tenant. Một message sai schema được thử lại theo cấu hình SQS rồi chuyển vào DLQ; alarm ghi nhận dữ liệu lỗi và gọi SNS thành công. CloudFront và lịch tự động vẫn tắt; nguồn API/HTML qua giao diện, Claude và kênh gửi báo cáo cần các thành viên phụ trách tích hợp và kiểm thử tiếp.

## Chạy lại

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.lock.txt
.venv\Scripts\ruff.exe check .
.venv\Scripts\python.exe -m pytest -q --basetemp build/pytest-local
.venv\Scripts\cfn-lint.exe infra/aws/template.yaml infra/aws/tenant-notifications.yaml
.venv\Scripts\python.exe -m scripts.validate_template
.venv\Scripts\cfn-lint.exe build/cloudformation.json
.venv\Scripts\python.exe -m scripts.demo
.venv\Scripts\python.exe -m scripts.demo_ingestion
```

Lệnh kiểm tra AWS và cách cấp tenant/secret được ghi trong [hướng dẫn triển khai](aws-deploy-free-plan.md). Smoke thật chỉ chạy khi có `--write-fixtures`; script API có `--cleanup` để xóa đúng các user/partition thử nghiệm vừa tạo. Script CSV giữ fixture và marker DLQ để xem bằng chứng, không xóa dữ liệu có sẵn hoặc thay cấu hình hàng đợi.

## Phần còn cần kiểm thử trên môi trường nhóm

- Cấu hình URL/endpoint nguồn đã có trong bản GitHub nhận ngày 04/11; cần thử nguồn API/HTML đầu cuối qua UI trên môi trường nhóm.
- Không ghi đã xác nhận gửi email nếu SNS subscription chưa được người nhận xác nhận; không ghi Slack hoặc Claude API thật đã đạt nếu chưa cấu hình secret và thử đúng kênh.
- Report lock và trạng thái gửi riêng từng kênh đã có trong bản hợp nhất. Cần thử lỗi/retry và giới hạn báo cáo trên AWS trước khi bật lịch tự động.
- Chưa có đo tải, SLA, thời gian tiết kiệm hoặc chi phí vận hành thực tế của toàn hệ thống. Không quy đổi số kiểm thử hoặc số commit thành tỷ lệ hoàn thành của cả nhóm.

## Dọn môi trường sau demo

Chỉ dọn fixture có ID ghi trong bằng chứng. Không xóa stack, làm rỗng bucket, xóa bảng hoặc lên lịch xóa KMS key theo một lệnh chung. Bucket/bảng/KMS/Slack secret có tài nguyên Retain; kiểm kê chủ sở hữu và phụ thuộc trước khi xử lý. KMS key, Secrets Manager, PITR và log có thể tiếp tục phát sinh chi phí.

Lần rollback SNS để lại key `7dc2a25d-6a05-4f30-8200-4ee7ee346530`, được tạo lúc 14:55 ngày 01/11/2026 (giờ Việt Nam). Key này không còn do stack quản lý; key đang dùng lấy từ output `NotificationKeyArn` của lần triển khai thành công. Chưa lên lịch xóa key giữ lại. Khi dọn, phải đối chiếu đúng ID, xác minh không có topic đang dùng và không nhầm với key của stack hiện tại.
