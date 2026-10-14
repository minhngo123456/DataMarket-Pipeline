# Báo cáo vấn đề bảo mật

Không đăng công khai thông tin có thể làm lộ dữ liệu, credential hoặc đường truy cập tenant. Kênh liên hệ bảo mật của nhóm chưa được thiết lập; hãy bổ sung địa chỉ liên hệ riêng trước khi repo được dùng với dữ liệu thật.

## Các điểm cần bảo vệ

- Mỗi request phải lấy tenant từ danh tính đã xác thực và membership phía máy chủ; không tin `tenant_id` do trình duyệt gửi lên.
- Áp dụng kiểm tra quyền theo vai trò và quota phía máy chủ.
- Hạn chế thời gian sống và phạm vi của presigned URL; kiểm tra file, kích thước và job trước khi xử lý.
- Phân trang/truy vấn, cursor, report link và object key phải giới hạn trong tenant hiện tại.
- Không ghi API key hoặc Slack webhook vào mã nguồn, log hay report; dùng kho secret phù hợp khi triển khai.
- Báo cáo AI cần nêu nguồn, thời điểm và dữ liệu thiếu/cũ; không được xem là quyết định giá tự động.

Thiết kế cách ly tenant được ghi tại [ADR 0001](docs/decisions/0001-tenant-isolation.md).
