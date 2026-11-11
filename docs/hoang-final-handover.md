# Bàn giao phần việc của Vũ Minh Hoàng

Kết quả kiểm thử cập nhật ngày 05/11 được tổng hợp trực tiếp bên dưới. Các kết quả ngày 01/11 và 04/11 được giữ làm lịch sử đối chiếu.

Nhóm 07, học phần Điện toán đám mây, dự án MarketData Pipeline. Bản hợp nhất ngày 04/11/2026 nhận `origin/main` tại commit `39c18f1`, trên nhánh `codex/hoang-final-integration`. Các kết quả AWS ngày 01/11 bên dưới thuộc phiên bản trước khi hợp nhất, dựa trên commit `e67e066` và phần sửa local của Hoàng.

## Hợp nhất ngày 04/11/2026

- Giữ NotificationFunction riêng và retry từng kênh của nhóm; kết hợp kiểm tra ARN theo tenant và Slack secret của Hoàng. Aggregator gọi bất đồng bộ hàm này sau khi lưu báo cáo.
- Dùng `AnthropicSecretArn` cho template; không đưa lại API key vào tham số deploy. Quyền Claude thuộc Aggregator, quyền SNS/Slack/KMS thuộc NotificationFunction.
- Giữ report lock và ID quan sát theo nội dung từ GitHub; receipt vẫn có ID theo job/dòng để mỗi job có bộ đếm riêng. Giữ transaction membership, quota và điều kiện bảo vệ catalog của Hoàng.
- Nhận cấu hình URL/header nguồn qua UI/API và kiểm tra đích HTTP công khai. API che giá trị header nhạy cảm khi trả cho người dùng không có quyền Admin.
- Bỏ global search, nút thông báo và trạng thái sức khỏe tĩnh chưa có chức năng ở khung giao diện. Giữ tìm kiếm đang hoạt động trong Catalog/Dashboard.

Tại mốc 04/11, bản hợp nhất chưa được triển khai lại lên AWS. Không dùng kết quả smoke ngày 01/11 để xác nhận NotificationFunction mới, chống trùng giữa nhiều job hoặc UI nguồn đã chạy trên môi trường thật. Script migration observation ID có chế độ dry-run; không chạy migration cùng thao tác đồng bộ Git.

Kiểm tra ngày 04/11: 239 kiểm thử Python đã đạt. Lần chạy trong sandbox có 5 test HTTP bị Windows chặn socket (WinError 10013); chạy lại toàn bộ nhóm HTTP ngoài sandbox đạt 35/35. Ruff, pip check và cfn-lint đạt; SAM dịch offline được 101 tài nguyên. Frontend đạt 34/34 Vitest, 3/3 test Node cho công cụ smoke và production build với cả hai cờ mock tắt. Bundle JavaScript còn khoảng 2.373 kB minified; chưa tối ưu chia chunk và vẫn có cảnh báo deprecation Antd trong Catalog.

## Cập nhật môi trường demo ngày 05/11/2026

Hoàng đã chạy 398 kiểm thử Python đạt và đóng gói thành công bằng SAM. Change set `samcli-deploy1793842593` đã được áp dụng lên `marketdata-hoang-dev` tại `us-east-1`; stack đạt `UPDATE_COMPLETE`. Đã bổ sung `NotificationFunction` và alarm lỗi tương ứng. Kiểm tra cấu hình sau cập nhật đạt 59/59; CloudFront và hai lịch ứng dụng vẫn tắt. Đây là kết quả kiểm tra cấu hình, chưa thay thế kiểm thử pipeline đầu cuối trên bản mới.

Guard đã được cập nhật bằng change set `demo-guard-20261105T062626`, chỉ bổ sung ARN của Lambda thông báo vào policy điều khiển concurrency. Guard đạt `UPDATE_COMPLETE`; 18/18 kiểm tra cấu hình đạt và dry-run nhận đủ 9 Lambda, 5 mapping SQS, 2 lịch và 1 rule. Email cảnh báo vẫn `active`; ngân sách và hạn dừng giữ nguyên. Phép thử SNS dừng/mở thật trước đó áp dụng cho 8 Lambda, chưa lặp lại trên phạm vi 9 Lambda. Bằng chứng cập nhật nằm trong `build/evidence/pre-demo-after-update.json` và `build/evidence/demo-guard-notification-result.json`.

### Kiểm thử CSV trên bản mới

Đã chạy qua S3 Raw → EventBridge → CSVWorker → TransformQueue → Normalizer trên AWS thật. File 100 dòng có 90 accepted, 10 rejected và 0 pending; phân vùng tenant A có 90 quan sát, tenant B có 0. Phát lại cùng job và cùng S3 VersionId, rồi gửi lại đủ 100 dòng tới Normalizer, vẫn giữ nguyên kết quả.

Đã tải lại nguyên bytes CSV thành job mới, dùng raw key và VersionId riêng. SHA-256 của hai phiên bản giống nhau; job mới cũng có 90 accepted và 10 rejected, tập 90 mã quan sát giữ nguyên, tenant B vẫn có 0 quan sát. Hai job có 100 receipt riêng cho mỗi job; bộ đếm và trạng thái completed của job gốc giữ nguyên. Kết quả này kiểm chứng chống trùng khi phát lại và khi tải cùng nội dung qua job mới, cùng tenant/nguồn.

Tạo hai tenant thử `smoke-a-9516436763b7`, `smoke-b-9516436763b7`; dữ liệu được giữ để đối chiếu. Tổng hợp kết quả nằm trong `build/evidence/pre-demo-csv-summary-20261105.json`; hai báo cáo gốc là `pre-demo-csv-20261105.json` và `pre-demo-csv-new-upload-20261105.json`. Script kiểm thử job mới được giữ tại `build/evidence/check_csv_new_upload.py`. Kế hoạch dọn ghi đúng hai phân vùng tenant và hai S3 key/VersionId trong `build/evidence/pre-demo-csv-cleanup-plan-20261105.json`; chưa thực hiện dọn và không dùng lệnh dọn toàn bộ smoke tenant.

Ngoài CSV, ngày 05/11 đã có kết quả riêng cho JWT/RBAC qua HTTP API, hai worker API/Web, partial batch, retry/DLQ, email cảnh báo và SNS theo tenant. Chi tiết cùng giới hạn của từng phép thử nằm trong [kết quả demo ngày 05/11](hoang-final-demo-results.md); không coi bằng chứng worker là kết quả toàn bộ giao diện hoặc lịch tự động.

## Đối chiếu Báo cáo tiến độ 2

44 giờ trong kế hoạch còn lại là ước tính đã nộp, không phải số giờ thực tế đã thực hiện. Bảng này chỉ ghi phần Hoàng phụ trách hoặc phối hợp, không nhận thay kết quả của cả nhóm.

| Mã | Phần việc của Hoàng | Sản phẩm bàn giao |
| --- | --- | --- |
| A1–A2 | Hợp nhất bản sửa hạ tầng, kiểm kê stack và kiểm thử CSV | Template có tên HTTP API, CloudFront theo điều kiện, script `verify_aws` và `smoke_test_aws` |
| A3 | IAM, SNS theo tenant, Secrets Manager và cảnh báo DLQ | Template, bộ đọc secret và kiểm tra namespace đích gửi. Đã nhận email alarm vận hành và thông báo SNS tenant; thử chéo tenant bị chặn, gọi lặp không gửi lại. Không thử Gemini/Slack. |
| A4 | Phối hợp bảo vệ membership | Transaction ghi hai bản ghi và phiên bản tenant; chặn ghi đè Cognito sub sang tenant khác; kiểm tra quota đồng thời |
| A7 | Hạ tầng cho worker API/web | Đã kiểm tra hai worker trên AWS bằng nguồn cấu hình qua API; có snapshot, trạng thái và lần thành công cuối trong DynamoDB. API/UI chưa hiển thị các trường sức khỏe nguồn. |
| A8 | Chuẩn hóa/catalog và cách ly tenant | Kiểm tra Decimal, UTC, SKU, chống trùng; transaction duyệt không chuyển cùng mã nguồn sang hai sản phẩm khác nhau |
| A13 | Cấu hình phân phối web và CORS | CloudFront không được tạo khi tắt; WebOrigin cho frontend local hoặc nhà cung cấp khác; không mở công khai bucket dữ liệu |
| A15 | Kiểm tra quyền và hai tenant | Kiểm thử giả lập; smoke API dùng Cognito SRP/JWT thật, kiểm tra Viewer, thu hồi quyền và truy cập chéo |
| A16 | Retry, partial batch, DLQ và log | CSV phát lại không tăng dữ liệu; partial batch chỉ trả record lỗi. Đã theo dõi 5 lượt APIWorker xử lý message JSON lỗi, chuyển vào API DLQ và nhận email alarm trên bản ngày 05/11. |
| A20 | Sửa lỗi tích hợp trong phạm vi trên | Chặn ký URL báo cáo ngoài tenant; hoàn thiện dependency/CI và bằng chứng kiểm thử |

## Những thay đổi cần giải thích khi bảo vệ

- Hạ tầng dùng chung nhưng mỗi thao tác dữ liệu phải có tenant. IAM giới hạn tài nguyên của Lambda; kiểm tra membership và khóa dữ liệu ở ứng dụng quyết định tenant nào được truy cập.
- `save_member` ghi `U#<sub>/MEMBERSHIP`, `T#<tenant>/MEMBER#<sub>` và cập nhật `META.membership_version` trong một transaction. Thao tác đồng thời phải đọc lại phiên bản và quota, không ghi hai bản độc lập.
- Bản hiện tại yêu cầu người dùng tự đăng ký/xác nhận email trước khi được mời. API lấy user bằng `AdminGetUser` rồi ghi membership có điều kiện; mời user đã thuộc tenant khác trả 409 và không thay đổi các bản ghi liên quan. Luồng này khác kế hoạch `AdminCreateUser` trong Báo cáo 2.
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

Chưa có kết quả `cfn-guard`: công cụ này không nằm trong yêu cầu báo cáo/CI hiện tại và người phụ trách chọn không cài thêm. cfn-lint kiểm tra cấu trúc template; regression test kiểm tra những quy tắc của dự án, không thay thế đánh giá compliance đầy đủ. CloudTrail Event History có ghi thao tác CloudFormation nhưng tài khoản chưa có trail lưu trữ dài hạn tại lần kiểm kê.

`receive_count` đọc tại DLQ là số lần nhận do SQS báo, không dùng để suy ra số lần Lambda đã retry. Bằng chứng về 5 lượt xử lý nằm trong log có đúng MessageId. Message được giữ ở DLQ để đối chiếu, không dùng lệnh purge hoặc xóa message khác.

## Nội dung có thể dùng cho báo cáo tổng kết

Các kết quả ngày 05/11 đã cập nhật chống trùng giữa hai job, worker API/Web, 5 lượt xử lý message JSON lỗi và thư SNS được người nhận xác nhận. Các kết quả ngày 01/11 ở trên chỉ là lịch sử. Chưa có cursor HTTP API hoặc trường sức khỏe nguồn trong `GET /sources`; không ghi hai nội dung này đã đạt.

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
- Ngày 05/11, người nhận đã xác nhận thư cảnh báo vận hành và một thông báo thử SNS theo tenant. Hai tenant thử A3 đã inactive; topic và bản ghi gửi được giữ để đối chiếu. Chưa kiểm thử Slack hoặc Gemini/Claude thật trong phạm vi của Hoàng.
- Report lock và trạng thái gửi riêng từng kênh đã có trong bản hợp nhất. Cần thử lỗi/retry và giới hạn báo cáo trên AWS trước khi bật lịch tự động.
- Chưa có đo tải, SLA, thời gian tiết kiệm hoặc chi phí vận hành thực tế của toàn hệ thống. Không quy đổi số kiểm thử hoặc số commit thành tỷ lệ hoàn thành của cả nhóm.

## Dọn môi trường sau demo

### Cơ chế tạm dừng đã kiểm thử ngày 05/11/2026

Stack phụ `marketdata-hoang-demo-guard` tại `us-east-1` đã đạt `CREATE_COMPLETE`. Ngân sách CUSTOM là 10 USD, cảnh báo qua email khi vượt 5 USD, gọi guard qua SNS khi vượt 8 USD và báo vượt ngân sách khi vượt 10 USD. Ngân sách tính chi phí toàn tài khoản trước credit/hoàn tiền; quyền điều khiển chỉ dành cho đúng ID của stack `marketdata-hoang-dev`.

Lịch gọi guard là 07:37:47 ngày 12/11/2026, giờ Việt Nam. Ngân sách theo dõi từ 05/11 đến trước 00:00 UTC ngày 16/11 để tính cả phí ghi nhận muộn. Đây không phải mức chặn tiền tuyệt đối.

Kết quả xác minh:

- 31 kiểm thử mới cho runtime, CLI và template đạt; Ruff và cfn-lint đạt. Không cài hoặc chạy cfn-guard theo lựa chọn của Hoàng.
- Change set chỉ thêm 15 tài nguyên guard; 18/18 kiểm tra cấu hình AWS đạt.
- Đã gửi một sự kiện SNS nội bộ để thử dừng, không tạo phí đến ngưỡng 8 USD. Đọc lại AWS xác nhận: 8 Lambda có concurrency 0; 5 mapping SQS, 2 lịch và 1 rule đều tắt.
- Lệnh mở lại đã hoàn tất; đối chiếu toàn bộ 16 thành phần khớp trạng thái trước kiểm thử. Hai lịch ứng dụng vốn đã tắt vẫn tắt. Guard và lịch dừng riêng vẫn hoạt động.
- Email cảnh báo đã được người nhận xác nhận; đọc trực tiếp qua API AWS ngày 05/11/2026 cho trạng thái `active`. Đường SNS tạm dừng đã được kiểm thử độc lập với email. Chưa thử nhận thư cảnh báo do vượt ngưỡng chi phí thật.

Lần tạo guard đầu rollback vì hai điều khoản SNS thiếu Sid riêng. Đã sửa, thêm kiểm thử hồi quy và dọn đúng stack lỗi cùng bucket cấu hình mới tạo được xác minh trống trước khi tạo lại. Không dọn bảng hoặc bucket dữ liệu ứng dụng. Bằng chứng cục bộ nằm trong `build/evidence/demo-guard-*.json`; CLI điều khiển lưu tại `scripts/demo_guard.py`.

### Dọn tài nguyên ứng dụng

Chỉ dọn fixture có ID ghi trong bằng chứng. Không xóa stack, làm rỗng bucket, xóa bảng hoặc lên lịch xóa KMS key theo một lệnh chung. Bucket/bảng/KMS/Slack secret có tài nguyên Retain; kiểm kê chủ sở hữu và phụ thuộc trước khi xử lý. KMS key, Secrets Manager, PITR và log có thể tiếp tục phát sinh chi phí.

Lần rollback SNS để lại key `7dc2a25d-6a05-4f30-8200-4ee7ee346530`, được tạo lúc 14:55 ngày 01/11/2026 (giờ Việt Nam). Key này không còn do stack quản lý; key đang dùng lấy từ output `NotificationKeyArn` của lần triển khai thành công. Chưa lên lịch xóa key giữ lại. Khi dọn, phải đối chiếu đúng ID, xác minh không có topic đang dùng và không nhầm với key của stack hiện tại.
