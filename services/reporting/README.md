# Reporting

Khu vực dành cho truy vấn lịch sử, tạo ma trận giá, tổng hợp báo cáo và thông báo. Báo cáo ngày dự kiến dùng cửa sổ 24 giờ và chạy lúc 08:00 Asia/Ho_Chi_Minh.

AI chỉ nhận số liệu tổng hợp cần thiết, kèm nguồn/thời điểm/giới hạn. Khi AI lỗi, dữ liệu số vẫn cần dùng được.

Hiếu phụ trách chính tổng hợp/AI và thông báo; Minh phụ trách UI, Hoàng hỗ trợ hạ tầng và truy vấn theo phân bổ giờ. Scheduler gọi Report Dispatcher, đưa job từng tenant vào ReportQueue; Aggregator lưu báo cáo trước khi gọi Notification Lambda. Không gán toàn bộ ma trận/truy vấn báo cáo riêng cho Hoàng.
