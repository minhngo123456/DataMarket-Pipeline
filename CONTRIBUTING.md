# Đóng góp

## Quy trình đề xuất

1. Mở issue mô tả mục tiêu hoặc lỗi và tenant/luồng dữ liệu bị ảnh hưởng.
2. Tạo nhánh ngắn từ nhánh mặc định: `feature/<ten-ngan>` hoặc `fix/<ten-ngan>`.
3. Giữ thay đổi tập trung vào một mục tiêu; cập nhật tài liệu hoặc hợp đồng dữ liệu nếu có thay đổi giao diện giữa các thành phần.
4. Mở pull request và điền checklist. Không gộp thay đổi làm suy yếu kiểm tra tenant, quyền hoặc quota.

## Checklist pull request

- [ ] Mô tả hành vi trước và sau thay đổi.
- [ ] Nêu thành phần và hợp đồng dữ liệu bị ảnh hưởng.
- [ ] Kiểm tra tenant được xác định phía máy chủ ở mọi API, worker và đường dẫn lưu trữ.
- [ ] Không đưa secret, dữ liệu khách hàng hoặc credential vào diff/log.
- [ ] Ghi rõ cách kiểm tra thủ công hoặc tự động đã thực hiện.
- [ ] Cập nhật tài liệu nếu thay đổi kiến trúc hoặc tiêu chí MVP.

Quy trình này là đề xuất khởi đầu; nhóm có thể điều chỉnh sau khi thống nhất cách review và bảo vệ nhánh trên GitHub.
