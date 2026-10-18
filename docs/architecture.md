# Kiến trúc dự kiến

> Tài liệu này ghi lại thiết kế trong báo cáo tiến độ. Đây chưa phải hạ tầng đã triển khai; các lựa chọn dịch vụ và cấu hình cần được xác minh trước khi đưa vào môi trường thật.

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
| S3 reports và Notification Lambda | Lưu báo cáo và gửi email hoặc Slack theo cấu hình. |
| CloudWatch và Secrets Manager | Theo dõi lỗi/queue/mức dùng và lưu secrets khi triển khai. |

## Luồng xử lý

1. Người dùng đăng nhập; backend xác định tenant từ danh tính và membership.
2. API tạo job và trả presigned URL cho trình duyệt. Trình duyệt PUT CSV trực tiếp vào S3 Raw tại `tenants/{tenant_id}/raw/{job_id}`. S3 Object Created qua EventBridge rule đưa sự kiện vào CSVQueue; worker đối chiếu bucket/key với job phía máy chủ.
3. Scheduler gọi Job Dispatcher Lambda; hàm đọc DynamoDB có phân trang, chọn tenant hoạt động và nguồn đang bật, kiểm tra quota rồi gửi job vào APIQueue/WebQueue. Retry cùng nguồn và kỳ lịch dùng job_id ổn định. MVP chỉ nhắm tới một API mẫu và HTML tĩnh được phép thu thập.
4. Worker xác thực nguồn/job, trích xuất bản ghi, rồi gửi dữ liệu hoặc tham chiếu tới queue chuẩn hóa.
5. Normalizer kiểm tra schema, giữ observation idempotent, ghép vào catalog tenant; ghép chưa chắc chắn chờ Analyst duyệt.
6. Dashboard truy vấn trong tenant, lọc dữ liệu quá hạn và hiển thị trạng thái nguồn.
7. Hằng ngày lúc 08:00 Asia/Ho_Chi_Minh, Scheduler gọi Report Dispatcher, tạo job từng tenant vào ReportQueue. Aggregator đọc cửa sổ 24 giờ, gọi Claude API, lưu số liệu/trạng thái trong DynamoDB và nội dung trong S3 Reports trước khi gọi Notification Lambda. Email qua SNS topic theo tenant, Slack qua webhook theo cấu hình. Link ứng dụng kiểm tra đăng nhập và tenant trước khi cấp quyền đọc báo cáo.

## Quy tắc an toàn và chịu lỗi

- `tenant_id` lấy từ ngữ cảnh đã xác thực hoặc metadata job do máy chủ tạo. Không lấy tenant từ CSV hay request do người dùng kiểm soát.
- Tenant phải hiện diện và được kiểm tra ở khóa truy vấn, object key, job, cursor, report và thông báo.
- Dữ liệu quá 24 giờ không tham gia ma trận báo cáo ngày; vẫn có thể hiển thị với nhãn quá hạn.
- Lỗi ở một nguồn không chặn nguồn khác. Khi AI lỗi, vẫn giữ số liệu và thể hiện trạng thái báo cáo.
- Mỗi queue có DLQ riêng. Worker SQS dự kiến timeout 45 giây, batch window 0, visibility timeout 300 giây, maxReceiveCount 5 và partial batch response. Batch size 10 là lựa chọn MVP. HTTP 429 thử lại có backoff; HTTP 403 cần kiểm tra quyền nguồn. Trạng thái từng kênh gửi giúp hạn chế gửi trùng, không bảo đảm gửi đúng một lần tới dịch vụ bên ngoài.
- Phân biệt thời điểm lưu UTC với thời gian hiển thị Asia/Ho_Chi_Minh.

## Câu hỏi cần chốt trước khi code

- Phần xử lý dữ liệu của Hoàng dùng Python; còn cần chốt phiên bản runtime, dependency và công nghệ các dịch vụ khác.
- Công cụ Infrastructure as Code, môi trường local và cách seed dữ liệu giả lập.
- Schema version cho job, raw record và observation; quy tắc xử lý tỷ giá và thuế.
- Danh sách endpoint, trang web được phép và chính sách sử dụng dữ liệu của nguồn.
- Chính sách lưu/xóa dữ liệu, quản trị tenant, log và khôi phục backup.
- Kênh bảo mật liên hệ, miền triển khai và cấu hình CORS.

## Phụ trách hạ tầng

Theo báo cáo đã nộp, Vũ Minh Hoàng phụ trách chính hạ tầng AWS/IAM, chuẩn hóa và kiểm tra cách ly dữ liệu, phối hợp với các thành viên theo [bảng phân công](team-work-split.md). Mock/local phục vụ phát triển song song, không loại phần AWS khỏi nhiệm vụ của nhóm.
