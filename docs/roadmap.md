# Lộ trình MVP dự kiến

> Kế hoạch 5 tuần và 576 giờ dưới đây là ước lượng trong báo cáo tiến độ, không xác nhận khối lượng đã làm hay mốc đã đạt.

| Tuần | Kết quả dự kiến | Tiêu chí cần kiểm tra |
| --- | --- | --- |
| 1 | Yêu cầu, UX, schema, vai trò, API contract, dữ liệu mẫu và nền tảng hạ tầng. | Hai tenant mẫu; thống nhất job và observation contract. |
| 2 | Đăng nhập, tenant, quota, khung CSV/API/web. | Hai tài khoản ở đúng tenant; Viewer bị chặn thao tác ghi; quota kiểm tra phía máy chủ. |
| 3 | Extractor, Normalizer, catalog, CSV và lịch sử giá. | Bộ 100 dòng có expected output; retry không nhân đôi observation. |
| 4 | Dashboard, ma trận 24 giờ, AI và thông báo. | Đối chiếu dữ liệu mẫu, loại giá quá hạn, kiểm tra lịch và múi giờ. |
| 5 | Kiểm tra đầu cuối, sửa lỗi, demo và báo cáo. | Truy cập chéo tenant bị từ chối; mô phỏng lỗi nguồn/AI/thông báo/retry. |

## Thứ tự triển khai gợi ý

1. Chốt schema và ranh giới tenant trước khi tách thành nhiều worker. Hoàng phụ trách chính hạ tầng AWS/IAM theo [phân công từ báo cáo đã nộp](team-work-split.md), không bàn giao toàn bộ AWS sang người ngoài nhóm.
2. Làm luồng CSV với fixture mẫu; tích hợp presigned upload, S3 Object Created qua EventBridge, CSVQueue, Extractor và TransformQueue/Normalizer. Fixture hỗ trợ phát triển song song với hạ tầng AWS.
3. Thêm Job Dispatcher sau lịch 6 giờ, một API mẫu và HTML tĩnh được phép thu thập; hiển thị trạng thái nguồn rõ ràng.
4. Hoàn thiện chuẩn hóa/duyệt SKU, truy vấn và ma trận giá trước khi tạo AI report. Luồng báo cáo dùng Scheduler 08:00 Asia/Ho_Chi_Minh, Report Dispatcher, ReportQueue, Aggregator và Notification.
5. Kiểm tra tenant và quyền xuyên suốt từ khi API/job đầu tiên xuất hiện, không đợi đến tuần cuối.

## Phạm vi kiểm soát

MVP không gồm thanh toán trực tuyến, tích hợp Excel trực tiếp, crawler trình duyệt cho web động, triển khai riêng từng khách hàng hoặc tự động điều chỉnh giá. Thu thập theo lịch 6 giờ không được mô tả là realtime. Chưa công bố SLA, độ chính xác, tiết kiệm thời gian hoặc lợi ích doanh thu nếu chưa đo.
