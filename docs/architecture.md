# Kiến trúc dự kiến

> Tài liệu này ghi lại thiết kế trong báo cáo tiến độ. Đây chưa phải hạ tầng đã triển khai; các lựa chọn dịch vụ và cấu hình cần được xác minh trước khi đưa vào môi trường thật.

## Thành phần

| Thành phần | Trách nhiệm dự kiến |
| --- | --- |
| Web app | Đăng nhập, cấu hình nguồn, tải CSV, xác nhận ghép SKU, xem ma trận và báo cáo. |
| Cognito và API Gateway | Xác thực người dùng bằng JWT và định tuyến API. |
| API Lambda | Kiểm tra membership, vai trò, quota; tạo job và presigned URL. |
| S3 raw | Lưu file gốc theo tiền tố tenant/job. |
| EventBridge Scheduler | Tạo lịch thu thập nguồn 6 giờ/lần và lịch tổng hợp báo cáo hằng ngày. |
| SQS và DLQ | Tách hàng đợi CSV, nguồn API/web, chuẩn hóa và báo cáo; cô lập lỗi retry. |
| Extractor / Normalizer | Đọc nguồn, kiểm tra trường, chuẩn hóa tiền tệ/thời gian và quản lý ghép SKU. |
| DynamoDB | Lưu catalog, quan sát giá, summaries và trạng thái báo cáo theo tenant. |
| Aggregator | Tổng hợp dữ liệu trong cửa sổ báo cáo; gửi số liệu đã tổng hợp cho Claude API. |
| S3 reports và Notification Lambda | Lưu báo cáo và gửi email hoặc Slack theo cấu hình. |
| CloudWatch và Secrets Manager | Theo dõi lỗi/queue/mức dùng và lưu secrets khi triển khai. |

## Luồng xử lý

1. Người dùng đăng nhập; backend xác định tenant từ danh tính và membership.
2. Tải CSV tạo job và URL tải lên có thời hạn. Sự kiện object mới đưa job vào queue CSV.
3. Scheduler đưa các job API/web đã cấu hình vào queue nguồn. MVP chỉ nhắm tới một API mẫu và HTML tĩnh được phép thu thập.
4. Worker xác thực nguồn/job, trích xuất bản ghi, rồi gửi dữ liệu hoặc tham chiếu tới queue chuẩn hóa.
5. Normalizer kiểm tra schema, giữ observation idempotent, ghép vào catalog tenant; ghép chưa chắc chắn chờ Analyst duyệt.
6. Dashboard truy vấn trong tenant, lọc dữ liệu quá hạn và hiển thị trạng thái nguồn.
7. Hằng ngày lúc 08:00 Asia/Ho_Chi_Minh, bộ tổng hợp xử lý cửa sổ 24 giờ, tạo report có report ID và gửi kênh đã cấu hình.

## Quy tắc an toàn và chịu lỗi

- `tenant_id` lấy từ ngữ cảnh đã xác thực hoặc metadata job do máy chủ tạo. Không lấy tenant từ CSV hay request do người dùng kiểm soát.
- Tenant phải hiện diện và được kiểm tra ở khóa truy vấn, object key, job, cursor, report và thông báo.
- Dữ liệu quá 24 giờ không tham gia ma trận báo cáo ngày; vẫn có thể hiển thị với nhãn quá hạn.
- Lỗi ở một nguồn không chặn nguồn khác. Khi AI lỗi, vẫn giữ số liệu và thể hiện trạng thái báo cáo.
- Retry có giới hạn, dùng DLQ và trạng thái từng kênh gửi để tránh gửi trùng.
- Phân biệt thời điểm lưu UTC với thời gian hiển thị Asia/Ho_Chi_Minh.

## Câu hỏi cần chốt trước khi code

- Ngôn ngữ/runtime cho web và Lambda.
- Công cụ Infrastructure as Code, môi trường local và cách seed dữ liệu giả lập.
- Schema version cho job, raw record và observation; quy tắc xử lý tỷ giá và thuế.
- Danh sách endpoint, trang web được phép và chính sách sử dụng dữ liệu của nguồn.
- Chính sách lưu/xóa dữ liệu, quản trị tenant, log và khôi phục backup.
- Kênh bảo mật liên hệ, miền triển khai và cấu hình CORS.
