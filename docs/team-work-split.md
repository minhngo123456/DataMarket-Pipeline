# Phân công MVP cho 4 người

> Phân công đề xuất dựa trên phạm vi MVP trong báo cáo tiến độ. Mỗi người có một mảng sở hữu từ đầu đến cuối và có thể dùng AI để tăng tốc triển khai. Việc dựng và vận hành AWS được bàn giao riêng; nhóm phát triển dùng mock/local adapters cho đến khi môi trường AWS sẵn sàng.

## Chủ sở hữu và đầu ra

| Người phụ trách | Mảng | Đầu ra cần bàn giao |
| --- | --- | --- |
| **Ngô Nguyễn Nhật Minh** | Trải nghiệm sản phẩm và web app | Luồng onboarding tenant; app shell và điều hướng; màn hình catalog/duyệt ghép SKU; Dashboard, ma trận so sánh và lịch sử giá; giao diện báo cáo; demo flow cho Admin, Analyst và Viewer. |
| **Phạm Văn Hướng** | Nhập và thu thập dữ liệu | CSV upload/parser, ánh xạ cột và báo lỗi theo dòng; adapter cho một REST API mẫu; extractor cho HTML tĩnh được phép thu thập; trạng thái job/nguồn và retry có giới hạn; fixture mẫu để nhóm tích hợp không cần gọi nguồn thật. |
| **Vũ Minh Hoàng** | Mô hình dữ liệu và xử lý giá | Hợp đồng job/observation có version; catalog theo tenant; chuẩn hóa SKU, giá, tiền tệ và thời điểm; hàng chờ duyệt ghép SKU; lịch sử 30 ngày và truy vấn ma trận 24 giờ; kiểm soát tenant ở repository/query và quota dữ liệu. |
| **Đỗ Huy Hiếu** | Tài khoản, API dùng chung và báo cáo | Middleware xác thực và RBAC Admin/Analyst/Viewer; membership và quota phía máy chủ; API report; tổng hợp dữ liệu ngày, tích hợp Claude với giới hạn token và dữ liệu dẫn chứng; adapter gửi email/Slack; xử lý trạng thái khi AI hoặc kênh gửi lỗi. |

## Ranh giới để tránh đụng việc

- Minh sở hữu UI và luồng thao tác. Hướng sở hữu adapter đọc nguồn và trạng thái ingestion; Hoàng sở hữu schema chuẩn đầu ra và ghi dữ liệu.
- Hoàng sở hữu catalog, mapping, observations và query giá. Hiếu sở hữu identity/access middleware, report API và luồng thông báo.
- Hiếu chốt cùng Minh hình dạng dữ liệu mà màn hình báo cáo cần. Hoàng cung cấp dữ liệu tenant-scoped; Hướng cung cấp job/source status theo contract chung.
- Mỗi hợp đồng API/job được ghi lại trước khi hai người cùng sửa một luồng. Thay đổi contract cần owner của phía phát ra và phía nhận cùng review.
- Không để tenant_id do trình duyệt hoặc CSV quyết định; backend gắn tenant từ danh tính/membership và metadata job.

## AWS là luồng bàn giao riêng

Bốn mảng trên không nhận việc provision hoặc vận hành AWS. Người phụ trách AWS cung cấp endpoint/config cần thiết, cơ chế xác thực, queue/table/bucket names, secret references và quyền IAM. Trước khi nhận được các giá trị đó, nhóm dùng mock/local adapters với cùng contract để vẫn phát triển và demo được.

## Nhịp tích hợp 5 tuần

| Tuần | Bàn giao chung |
| --- | --- |
| 1 | Minh chốt luồng người dùng; Hướng, Hoàng và Hiếu thống nhất source/job/observation/API contracts cùng dữ liệu mẫu. |
| 2 | Minh dựng app shell; Hướng chạy được CSV bằng fixture; Hoàng có schema/repository và chuẩn hóa; Hiếu có auth stub, RBAC và contract API. |
| 3 | Nối CSV vào catalog/lịch sử giá; thêm API mẫu và HTML fixture; xác nhận SKU thủ công; hai tenant dùng dữ liệu tách biệt. |
| 4 | Nối dashboard/ma trận 24 giờ; chạy aggregation và AI report; thêm thông báo giả lập hoặc sandbox; xử lý dữ liệu cũ và lỗi nguồn. |
| 5 | Chạy demo đầu cuối bằng fixtures; rà soát phân quyền và truy cập chéo tenant; sửa lỗi; chuẩn bị tài liệu demo. |

## Quy tắc làm việc khi vibe code

1. Mỗi người tạo nhánh cho một thay đổi có mục tiêu và mở PR nhỏ; ghi rõ file/contract nào mình sở hữu.
2. Dùng fixtures và mock trước, không khóa tiến độ vào credential hay hạ tầng AWS.
3. Người khác review luồng dữ liệu và ranh giới tenant; người viết tự kiểm tra thay đổi và ghi lại cách kiểm tra trong PR.
4. Chốt quyết định schema/API ở tuần 1; sau đó cập nhật tài liệu contract trước khi đổi format.
5. Ưu tiên một luồng chạy trọn vẹn: tải CSV → duyệt SKU → xem giá → xem báo cáo. Không mở rộng scraper hoặc AI trước khi luồng này ổn định.
