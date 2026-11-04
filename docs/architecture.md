# Kiến trúc và trạng thái tích hợp

> Tài liệu mô tả các thành phần trong mã nguồn và template SAM. Kết quả kiểm tra AWS của phần Hoàng được ghi riêng trong [bàn giao tổng kết](hoang-final-handover.md); khai báo hạ tầng không tự chứng minh luồng nghiệp vụ đã chạy đầu cuối.

## Thành phần

| Thành phần | Trách nhiệm dự kiến |
| --- | --- |
| Web app | Đăng nhập, cấu hình nguồn, tải CSV, xác nhận ghép SKU, xem ma trận và báo cáo. |
| Cognito và API Gateway | Xác thực người dùng bằng JWT và định tuyến API. |
| API Lambda | Kiểm tra membership, vai trò, quota; tạo job và presigned URL. |
| S3 raw | Lưu file gốc theo tiền tố tenant/job. |
| EventBridge Scheduler | Kích hoạt Job Dispatcher mỗi 6 giờ và Report Dispatcher lúc 08:00 Asia/Ho_Chi_Minh. |
| Job Dispatcher Lambda | Đọc tenant hoạt động, nguồn đang bật và cấu hình/quota từ DynamoDB; tạo job theo nguồn/kỳ lịch vào APIQueue hoặc WebQueue. |
| Report Dispatcher Lambda | Đọc tenant hoạt động và đưa job báo cáo theo tenant/ngày vào ReportQueue. |
| SQS và DLQ | Tách hàng đợi CSV, nguồn API/web, chuẩn hóa và báo cáo; cô lập lỗi retry. |
| Extractor / Normalizer | Đọc nguồn, kiểm tra trường, chuẩn hóa tiền tệ/thời gian và quản lý ghép SKU. |
| DynamoDB | Lưu catalog, quan sát giá, summaries và trạng thái báo cáo theo tenant. |
| Aggregator | Tổng hợp dữ liệu trong cửa sổ báo cáo; gửi số liệu đã tổng hợp cho Claude API. |
| S3 Reports và module thông báo | Lưu báo cáo; Aggregator gọi module gửi email hoặc Slack theo cấu hình tenant. |
| CloudWatch và Secrets Manager | Theo dõi lỗi/queue/mức dùng và lưu secrets khi triển khai. |

## Luồng xử lý

1. Người dùng đăng nhập; backend xác định tenant từ danh tính và membership.
2. API tạo job và trả presigned URL cho trình duyệt. Trình duyệt PUT CSV trực tiếp vào S3 Raw tại `tenants/{tenant_id}/raw/{job_id}`. S3 Object Created qua EventBridge rule đưa sự kiện vào CSVQueue; worker đối chiếu bucket/key với job phía máy chủ.
3. Scheduler gọi Job Dispatcher Lambda; hàm đọc DynamoDB có phân trang, chọn tenant hoạt động và nguồn đang bật, kiểm tra quota rồi gửi job vào APIQueue/WebQueue. Retry cùng nguồn và kỳ lịch dùng job_id ổn định. MVP chỉ nhắm tới một API mẫu và HTML tĩnh được phép thu thập.
4. Worker xác thực nguồn/job, trích xuất bản ghi, rồi gửi envelope JSON tới TransformQueue; mỗi dòng tối đa 32 KiB. Chưa có contract đọc bản ghi qua tham chiếu S3.
5. Normalizer kiểm tra schema, giữ observation idempotent, ghép vào catalog tenant; ghép chưa chắc chắn chờ Analyst duyệt.
6. Dashboard truy vấn trong tenant, lọc dữ liệu quá hạn và hiển thị trạng thái nguồn.
7. Lịch báo cáo được cấu hình lúc 08:00 Asia/Ho_Chi_Minh và vẫn tắt khi tích hợp. Report Dispatcher tạo job từng tenant vào ReportQueue. Aggregator giữ report lock, đọc cửa sổ 24 giờ, tạo nhận định, lưu chỉ mục DynamoDB và nội dung S3 Reports trước khi gọi NotificationFunction bất đồng bộ. Hàm thông báo lưu trạng thái và thử lại riêng từng kênh. Email qua SNS topic riêng, Slack đọc webhook từ secret của tenant. API kiểm tra đăng nhập, tenant và khóa S3 trước khi cấp URL đọc báo cáo.

## Quy tắc an toàn và chịu lỗi

- `tenant_id` lấy từ ngữ cảnh đã xác thực hoặc metadata job do máy chủ tạo. Không lấy tenant từ CSV hay request do người dùng kiểm soát.
- Tenant phải hiện diện và được kiểm tra ở khóa truy vấn, object key, job, cursor, report và thông báo.
- Dữ liệu quá 24 giờ không tham gia ma trận báo cáo ngày; vẫn có thể hiển thị với nhãn quá hạn.
- Lỗi ở một nguồn không chặn nguồn khác. Khi AI lỗi, vẫn giữ số liệu và thể hiện trạng thái báo cáo.
- Mỗi queue có DLQ riêng. Worker dữ liệu timeout 45 giây, visibility timeout 300 giây; Aggregator timeout 120 giây, ReportQueue visibility 720 giây. maxReceiveCount là 5 và bật partial batch response. Extractor và Aggregator nhận mỗi lô một job, Normalizer tối đa 10 dòng. NotificationFunction dùng retry bất đồng bộ và lưu kết quả từng kênh để không gửi lại kênh đã thành công.
- Phân biệt thời điểm lưu UTC với thời gian hiển thị Asia/Ho_Chi_Minh.

## Cấu hình đã chốt và phần cần tích hợp

- Backend dùng Python 3.12, boto3 và AWS SAM; kiểm thử local bằng pytest/Moto. Giao diện dùng React.
- Stack `marketdata-hoang-dev` đã triển khai tại `us-east-1`, CloudFront và lịch tự động tắt. CSV/replay, quyền API và kiểm kê cấu hình đã đạt trên AWS; bằng chứng trong tài liệu bàn giao.
- Bản hợp nhất 04/11 đã có cấu hình URL/header nguồn qua UI/API, report lock và retry thông báo theo kênh. Cần kiểm thử các phần này trên AWS; phân trang/cursor còn cần hoàn thiện.
- Danh sách endpoint, trang web được phép và chính sách sử dụng dữ liệu của nguồn.
- Chính sách lưu/xóa dữ liệu, quản trị tenant, log và khôi phục backup.
- Kênh bảo mật liên hệ, miền triển khai và cấu hình CORS.

## Phụ trách hạ tầng

Theo báo cáo đã nộp, Vũ Minh Hoàng phụ trách chính hạ tầng AWS/IAM, chuẩn hóa và kiểm tra cách ly dữ liệu, phối hợp với các thành viên theo [bảng phân công](team-work-split.md). Mock/local phục vụ phát triển song song, không loại phần AWS khỏi nhiệm vụ của nhóm.

## Phần đã triển khai

Stack đã kiểm thử ngày 01/11 có CSV/API/Web worker, Normalizer, hai Dispatcher, ApiFunction với 21 route và ReportAggregator. Bản mã hợp nhất ngày 04/11 thêm NotificationFunction riêng và chưa triển khai lại trên stack Hoàng. SNS/Slack theo tenant có template cấp tài nguyên riêng; chưa cấu hình kênh báo cáo thật trong lần kiểm thử của Hoàng. CloudFront không được tạo khi EnableCloudFront=DISABLED; frontend có thể chạy cục bộ với backend AWS.
