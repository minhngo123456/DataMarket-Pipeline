# Hạ tầng AWS dự kiến

Theo báo cáo đã nộp, Vũ Minh Hoàng phụ trách chính hạ tầng và IAM, phối hợp với Phạm Văn Hướng và Đỗ Huy Hiếu. Hạng mục hạ tầng/IAM có 56 giờ: Minh 0, Hướng 8, Hoàng 40, Hiếu 8; tuần 1-2. Các giờ tham gia xác thực, thu thập, chuẩn hóa và kiểm thử được ghi riêng trong [bảng phân công](../../docs/team-work-split.md).

Phạm vi thiết kế gồm CloudFront/S3 Web, Cognito, API Gateway/Lambda, S3 Raw/Reports, EventBridge Object Created, hai lịch Scheduler và hai Dispatcher, CSVQueue/APIQueue/WebQueue/TransformQueue/ReportQueue với DLQ riêng, DynamoDB/GSI theo tenant, Secrets Manager và CloudWatch. CloudFront/IAM có phạm vi toàn cầu; AWS Region cho tài nguyên xử lý cần chốt.

S3 riêng tư; CloudFront truy cập S3 Web qua OAC. Quyền IAM theo chức năng; worker xác minh tenant/job và source. Cấu hình dự kiến SQS/Lambda và các luồng được ghi trong [kiến trúc](../../docs/architecture.md).

Chưa chọn công cụ IaC hoặc triển khai tài nguyên. Cần chốt Region, quyền tài khoản, runtime, cấu hình và quy trình triển khai trước khi code. Không commit state, secrets hoặc credentials. Mock/local chỉ hỗ trợ phát triển song song, không thay thế phần AWS và kiểm thử tích hợp.
