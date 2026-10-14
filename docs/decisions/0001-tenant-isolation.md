# ADR 0001: Cách ly dữ liệu theo tenant

- **Trạng thái:** đề xuất theo thiết kế MVP
- **Bối cảnh:** dịch vụ dùng chung hạ tầng cho nhiều doanh nghiệp; dữ liệu giá, catalog, báo cáo và job phải riêng theo doanh nghiệp.

## Quyết định đề xuất

- Xác định tenant từ danh tính đã xác thực và membership phía máy chủ.
- Gắn tenant vào job, object key, khóa dữ liệu, cursor và report.
- Kiểm tra membership/role/quota tại API trước khi tạo công việc.
- Worker xác minh tenant từ metadata job do máy chủ tạo; không tin trường tenant trong nội dung CSV.
- Kiểm tra quyền lại khi đọc báo cáo hoặc tạo URL truy cập.

## Hệ quả

- Các hợp đồng nội bộ cần mang định danh tenant và mọi truy vấn cần tenant scope.
- Cần kiểm tra truy cập chéo tenant với hai tenant mẫu ở API, job, cursor và report link.
- Hạ tầng chia sẻ vẫn có nguy cơ một tenant sử dụng quá nhiều tài nguyên; quota và giới hạn concurrency cần được thiết kế.
- Cần xem lại quyết định nếu xuất hiện yêu cầu residency, deployment riêng hoặc isolation mạnh hơn.
