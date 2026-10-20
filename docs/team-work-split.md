# Phân công theo báo cáo tiến độ đã nộp

Nguồn đối chiếu: `2627I_INT3319_1_Progress_Report_1_Group_7.pdf`, Chương 3, trang 8. Nhóm 07, học phần Điện toán đám mây. Các số dưới đây là số giờ cần thiết dự kiến, không phải số giờ đã thực hiện.

## Trách nhiệm chính

| Thành viên | Trách nhiệm theo mục 3.2 và 3.5 |
| --- | --- |
| Ngô Nguyễn Nhật Minh | Chốt yêu cầu và trải nghiệm người dùng, giao diện/Dashboard; cùng rà soát ánh xạ catalog. |
| Phạm Văn Hướng | Kết nối nguồn CSV, REST API mẫu và HTML tĩnh được phép; trạng thái nguồn, fixture và xử lý lỗi thu thập. |
| Vũ Minh Hoàng | Hạ tầng AWS và IAM; tham gia chuẩn hóa/catalog, kiểm tra khóa tenant và cách ly dữ liệu. Phối hợp thống nhất schema, dữ liệu mẫu và truy vấn theo tenant. |
| Đỗ Huy Hiếu | Đăng nhập, membership/RBAC và quota phía máy chủ; API, tổng hợp/Claude API, thông báo và giới hạn token/mức dùng. |

Không tách việc dựng AWS sang một người ngoài bốn thành viên như phân công trước đây. Hoàng phụ trách chính hạ tầng/IAM; các phần xác thực, quyền ứng dụng và quota do Hiếu phụ trách chính, có Hoàng phối hợp theo bảng giờ. Các hạng mục có nhiều người cùng tham gia, không giao toàn bộ catalog hoặc truy vấn giá riêng cho Hoàng.

## Hạng mục công việc và số giờ cần thiết

| WBS / công việc | Liên hệ kiến trúc | Tổng giờ | Nhật Minh | Văn Hướng | Minh Hoàng | Huy Hiếu | Tuần |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1. Yêu cầu, UX và schema | F1-F4; mẫu dữ liệu, quyền, catalog | 40 | 24 | 8 | 8 | 0 | 1 |
| 2. Hạ tầng và IAM | F5; S3, SQS/DLQ, EventBridge, DB | 56 | 0 | 8 | 40 | 8 | 1-2 |
| 3. Đăng nhập, tenant, quota | F1/F5; Cognito, API, membership | 64 | 16 | 0 | 16 | 32 | 1-2 |
| 4. CSV, API, web tĩnh | F2; 3 extractor và trạng thái nguồn | 96 | 0 | 64 | 24 | 8 | 2-3 |
| 5. Catalog và Normalizer | F3; ánh xạ SKU, khóa tenant, idempotency | 64 | 16 | 24 | 24 | 0 | 3 |
| 6. Tổng hợp và AI | F4; ma trận, Claude API, report_id | 56 | 0 | 8 | 0 | 48 | 3-4 |
| 7. Dashboard, API và gửi tin | F1/F4; query API, UI, SNS/Slack | 88 | 56 | 0 | 8 | 24 | 2-4 |
| 8. Kiểm thử và giám sát | F1-F5; E2E, tenant, quota, retry/DLQ | 64 | 8 | 24 | 24 | 8 | 4-5 |
| 9. Tài liệu và demo | Toàn hệ thống; hướng dẫn, báo cáo | 32 | 24 | 8 | 0 | 0 | 5 |
| 10. Dự phòng sửa lỗi | Tích hợp luồng dịch vụ và API/AI | 16 | 0 | 0 | 0 | 16 | 5 |
| Tổng | Toàn bộ 5 phân hệ kiến trúc | 576 | 144 | 144 | 144 | 144 | 5 |

Tổng 576 giờ gồm 560 giờ công việc và 16 giờ dự phòng. Mỗi người 144 giờ trong 5 tuần, trung bình 28,8 giờ/tuần. Cột Tuần là lịch dự kiến. Riêng Hoàng: 8 + 40 + 16 + 24 + 24 + 0 + 8 + 24 + 0 + 0 = 144 giờ.

## Phối hợp và thứ tự tích hợp

- Tuần 1: cả nhóm chốt source/job/observation/API contracts và dữ liệu mẫu; Minh chốt yêu cầu, Hoàng chuẩn bị hạ tầng/IAM.
- Tuần 2: Cognito, tenant, quota; nhánh CSV và khung API/web. Hiếu phụ trách quyền ứng dụng, Hoàng phối hợp quyền hạ tầng và kiểm thử tenant ngay khi API xuất hiện.
- Tuần 3: nối extractor với Normalizer/catalog, lịch sử giá và hàng chờ duyệt SKU. Hướng cung cấp schema/bản ghi mẫu; Hoàng cùng Minh và Hướng kiểm tra chuẩn hóa và chống trùng.
- Tuần 4: Dashboard, ma trận 24 giờ, AI và thông báo. Hiếu nhận dữ liệu chuẩn/chỉ mục thời gian; Minh tích hợp UI; Hoàng hỗ trợ truy vấn và giám sát.
- Tuần 5: kiểm thử đầu cuối, truy cập chéo tenant, lỗi nguồn/AI/thông báo và retry/DLQ; sửa lỗi và demo.

Có thể dùng fixture/mock để phát triển song song, nhưng chúng không thay thế phần hạ tầng AWS và kiểm thử triển khai trong báo cáo. Khi thiếu thời gian, ưu tiên CSV và API, giảm phạm vi scraper trước khi giảm kiểm thử cách ly dữ liệu.

## Ranh giới triển khai

Hướng phụ trách extractor; Hoàng phối hợp hạ tầng và chuẩn hóa. Hiếu sở hữu API xác thực/quota và luồng báo cáo; Minh sở hữu UI. Thay đổi contract phải được phía phát và phía nhận cùng review. Không lấy tenant từ trình duyệt hoặc nội dung CSV; backend và worker xác minh theo membership, job và source phía máy chủ.

Bản local đã có code Python, hạ tầng bằng SAM và kiểm thử giả lập cho phần của Hoàng (xem [bàn giao của Hoàng](hoang-handover.md)) và đầy đủ 3 Ingestion Extractor của Hướng (xem [bàn giao của Hướng](huong-handover.md)). Không coi tài liệu hoặc mock là bằng chứng hệ thống AWS đã chạy. Bảng giờ là phân bổ dự kiến, không phải số giờ đã làm.
