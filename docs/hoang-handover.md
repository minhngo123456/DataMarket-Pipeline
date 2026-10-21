# Phần việc của Vũ Minh Hoàng

Code dùng Python 3.12, boto3 và AWS SAM. Mục tiêu là một luồng dễ chạy, dễ giải thích trong đồ án. Đã kiểm tra local đạt chuẩn và đẩy lên nhánh tính năng để mở Pull Request; chưa triển khai AWS thật.

## Đã có

| Phần việc theo báo cáo | File và đầu ra |
| --- | --- |
| Hạ tầng/IAM, 40 giờ phân bổ trong hạng mục 2 | `infra/aws/template.yaml`: S3 riêng tư, CloudFront/OAC, Cognito, HTTP API/JWT authorizer, DynamoDB/GSI, 5 queue/DLQ, 2 Dispatcher, worker CSV/Normalizer, log và cảnh báo DLQ. |
| Phối hợp schema, đăng nhập và tenant | `marketdata/contracts.py`, `access.py`: mã công việc/dòng ổn định, membership lấy phía máy chủ, kiểm tra vai trò và thu hồi quyền. |
| Phối hợp thu thập | `services/ingestion/csv_worker.py`: adapter CSV tham chiếu cho Hướng; đọc đúng phiên bản S3 gắn với job, gửi từng dòng sang TransformQueue, kiểm tra lỗi gửi một phần. |
| Chuẩn hóa và catalog | `marketdata/normalize.py`, `service.py`, `repository.py`: Decimal, UTC, ánh xạ SKU xác nhận, hàng chờ duyệt, ghi có điều kiện, transaction hạn mức catalog. |
| Hỗ trợ truy vấn | `marketdata/queries.py`: lịch sử tối đa 30 ngày, ma trận 24 giờ theo tenant, giá mới nhất của mỗi nguồn. |
| Kiểm thử cách ly và chịu lỗi | `tests/`: hai tenant, giả mạo job/source, membership bị vô hiệu hóa, Viewer, retry, lỗi từng dòng, gửi SQS thất bại, phiên bản CSV bị thay đổi và lịch điều phối. |

Số giờ trong báo cáo là phân bổ ban đầu, không phải thời gian đã thực hiện. Các phần phối hợp không có nghĩa Hoàng đã hoàn thành toàn bộ API, extractor hoặc báo cáo của các bạn khác.

## Chạy ở máy cá nhân

Từ thư mục gốc, dùng Python 3.12:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.lock.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\ruff.exe check .
.venv\Scripts\cfn-lint.exe infra/aws/template.yaml
.venv\Scripts\python.exe -m scripts.validate_template
.venv\Scripts\cfn-lint.exe build/cloudformation.json
.venv\Scripts\python.exe -m scripts.demo
```

Trên Linux/macOS, thay `.venv\Scripts\python.exe` bằng `.venv/bin/python` và dùng executable tương ứng trong `.venv/bin`.

Demo gọi worker CSV, SQS và Normalizer qua Moto trong cùng tiến trình. Dữ liệu gồm 100 dòng: 90 dòng hợp lệ, 10 dòng sai giá. File được xử lý hai lần nhưng tenant A vẫn có 90 quan sát, tenant B có 0. Thời điểm demo cố định ở `2026-10-19T01:00:00Z` để fixture không hết hạn theo ngày chạy máy. Đây là kiểm thử giả lập AWS, không phải bằng chứng IAM hoặc dịch vụ AWS thật đã hoạt động.

## Kết quả kiểm tra local ngày 19/10/2026

- pytest: 41 kiểm thử đạt; sau khi bổ sung version vào bản ghi lưu trữ, kiểm tra lại riêng chuẩn hóa/retry và duyệt SKU cũng đạt.
- ruff check và pip check đạt.
- SAM dịch offline thành 54 tài nguyên CloudFormation; cfn-lint kiểm tra cả template nguồn và bản đã dịch không báo lỗi. Kiểm thử xác nhận hai worker chỉ có quyền nhận/xóa message trên queue của mình.
- scripts.demo: xử lý file hai lần vẫn có accepted=90, rejected=10, pending=0; tenant A có 90 quan sát, tenant B có 0.
- Chưa chạy sam deploy, IAM sandbox, đăng nhập Cognito thật, tải web hoặc gọi Claude/Slack/SNS thật. Đã hoàn thành toàn bộ kiểm thử local trước khi tạo commit và đẩy lên GitHub.

## Bàn giao cho Hướng

Mỗi dòng vào TransformQueue dùng JSON:

```json
{
  "schema_version": 1,
  "tenant_id": "tenant-a",
  "source_id": "vendor",
  "job_id": "csv-job",
  "row_number": 1,
  "record": {
    "source_sku": "001-PHONE",
    "price": "20100000",
    "currency": "VND",
    "observed_at": "2026-10-19T07:45:00+07:00",
    "price_basis": "tax_included"
  }
}
```

- Job/source phải được tạo phía máy chủ, cùng tenant, đúng loại và chưa bị hủy. Worker API/HTML cập nhật `row_count` của job trước khi gửi dòng; số dòng giữ ổn định qua retry.
- `row_number` bắt đầu từ 1, tối đa 1000. Cùng tenant/source/job/row_number tạo cùng observation_id. Không tái dùng job cho nội dung nguồn mới.
- Giá dùng chuỗi số thập phân, không có dấu phân cách hàng nghìn. SKU được chuẩn hóa Unicode, bỏ khoảng trắng đầu/cuối và viết hoa, vẫn giữ số 0 đầu. Không ghép mờ tự động.
- Không đưa `tenant_id` vào `record`. Worker lấy tenant từ metadata job, không từ CSV.
- Bản này nhận JSON trực tiếp, tối đa 32 KiB/dòng; CSV UTF-8 tối đa 1 MiB và 1000 dòng. Chưa có nhánh đọc JSON qua tham chiếu S3. Muốn mở rộng phải bổ sung contract và kiểm thử trước.
- `send_rows` chia lô tối đa 10 message và 240 KiB. Nếu một phần gửi lỗi, worker thử lại công việc; Normalizer bỏ qua các receipt đã có.
- Job Dispatcher tạo job API/web từ nguồn đang bật mỗi kỳ 6 giờ. API/HTML worker do Hướng viết và hiện được nối vào APIQueue/WebQueue trong template. Template vẫn chưa được deploy lên AWS thật.

## Bàn giao cho Hiếu và Minh

Hiếu nối API với các hàm trong `marketdata/access.py`. Tham số `verified_user_id` là `sub` của JWT đã được xác minh, tuyệt đối không lấy từ JSON/body. Các hàm tự tra membership để tìm tenant; không nhận tenant do trình duyệt chọn.

Membership lưu hai bản ghi trong ControlTable: `pk=U#<sub>, sk=MEMBERSHIP` chứa tenant_id/status; `pk=T#<tenant>, sk=MEMBER#<sub>` chứa role/status. API tạo hoặc thu hồi hai bản ghi bằng transaction. Các lần đọc vẫn kiểm tra bản ghi phía tenant để thu hồi quyền ngay.

API owner chịu trách nhiệm xác minh JWT, quota 3 nguồn/5 thành viên/1 báo cáo AI mỗi ngày, upload URL, phân trang/cursor và route HTTP. `ApiDataPolicyArn` là policy dữ liệu để gắn vào role API khi các kiểm tra đó đã có; policy không tự cách ly tenant nếu handler bỏ qua membership.

Normalizer ghi receipt `accepted`, `pending` hoặc `rejected`, kèm mã lỗi khi có. SKU chờ duyệt không xuất hiện trong ma trận. Analyst/Admin gọi `approve_pending`, chọn product trong cùng tenant; việc ghi mapping, chuyển trạng thái và tạo quan sát dùng một transaction. API phải xử lý `Conflict` nếu hai người duyệt cùng một dòng. Viewer chỉ đọc.

Giới hạn 100 SKU kiểm tra bằng transaction khi thêm product. Đếm SKU của file sau khi đọc và duyệt catalog; không giả định API biết nội dung CSV trước khi nhận file. Khi đạt quota, không tạo product thứ 101.

`read_matrix` chỉ so sánh giá cùng product đã xác nhận và cùng điều kiện thuế, dùng cửa sổ `[now - 24 giờ, now)`. `price_basis=unknown` vẫn lưu lịch sử nhưng không tham gia so sánh. MVP không tự cộng thuế/phí vận chuyển. Tỷ giá, nếu bật, phải nằm trong cấu hình source phía máy chủ, có ngày hiệu lực và được giữ cùng giá gốc.

Hiếu nhận ReportQueue để viết Aggregator và Notification. Các hàm này cùng Claude API, SNS topic/subscription theo tenant và Slack secret chưa được viết trong phần Hoàng. Minh nhận kết quả truy vấn và receipt/job counts để nối UI. Decimal phải đổi sang chuỗi khi trả JSON, không ép qua float.

## Giới hạn cần nhớ khi tích hợp

- Template tạo HTTP API và JWT authorizer, chưa có business route. Cognito cho tạo user bởi admin; onboarding qua API do Hiếu nối tiếp. CloudFront chưa có giao diện cho đến khi Minh tải bản build vào WebBucket; CORS phải đổi sang đúng WebOrigin.
- GSI theo ngày đồng bộ trễ. Query không bảo đảm snapshot nhất quán; Aggregator cần lưu thời điểm chốt dữ liệu và số nguồn thành công như báo cáo.
- TTL xóa nền, nên code vẫn lọc cửa sổ 30 ngày. Receipt pending hết hạn không được duyệt. Giá thiếu nguồn hoặc quá 24 giờ không được mô tả là giá hiện tại.
- `refresh_job` đếm receipt của tenant rồi tính trạng thái. Cách này dễ kiểm tra ở quy mô fixture/Pilot; chưa tối ưu chi phí đọc cho khối lượng lớn. Chưa đo tải, chi phí hoặc thời gian chạy Lambda thật.
- CI và Moto không thay thế thử IAM thật. Trước demo AWS cần kiểm tra hai tenant, quyền từng role, S3, lịch, DLQ, quota và log bằng tài khoản sandbox của nhóm.

Tham chiếu AWS: [SQS/Lambda và partial batch](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html), [TTL](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/TTL.html), [SAM Function](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/sam-resource-function.html).
