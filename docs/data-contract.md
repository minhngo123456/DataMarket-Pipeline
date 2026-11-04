# Hợp đồng dữ liệu khởi đầu

> Phần worker Python đã dùng message schema_version=1, kiểm tra tại marketdata/contracts.py. Ví dụ Observation dưới đây mô tả bản ghi đầu ra; đầu vào TransformQueue và giới hạn tích hợp được chốt ở docs/hoang-handover.md. Schema các API nghiệp vụ còn cần Hiếu và Minh thống nhất.

## Thực thể

| Thực thể | Trường cần có | Ghi chú |
| --- | --- | --- |
| Tenant | `tenant_id`, trạng thái, gói | Được tạo/quản lý ở phía dịch vụ. |
| Membership | `tenant_id`, `user_id`, `role` | Vai trò dự kiến: `admin`, `analyst`, `viewer`. |
| Source | `tenant_id`, `source_id`, loại, cấu hình, trạng thái | Secret chỉ lưu tham chiếu tới kho secret. |
| Job | `job_id`, `tenant_id`, `source_id`, loại, trạng thái | Tenant do backend gắn; status cần truy vết retry. |
| Product | `tenant_id`, `product_id`, SKU, tên và thuộc tính biến thể | Catalog tách theo tenant trong MVP. |
| Observation | tenant, product, source, giá, currency, observed_at | Giữ giá gốc; thời gian lưu theo UTC. |
| Summary / Report | `tenant_id`, `report_id`, kỳ dữ liệu, trạng thái | Số liệu và nhận định phải liên kết cùng report/kỳ. |

## Quan sát giá đề xuất

```json
{
  "schema_version": 1,
  "tenant_id": "server-assigned",
  "job_id": "job-id",
  "source_id": "source-id",
  "observation_id": "stable-id-for-retry",
  "product_id": "tenant-product-id",
  "source_sku": "vendor-sku",
  "price": 125000,
  "currency": "VND",
  "observed_at": "2026-10-14T01:00:00Z",
  "match_status": "confirmed"
}
```

Ví dụ trên là Observation sau chuẩn hóa, không phải envelope đầu vào TransformQueue. Code giữ thêm giá/tiền tệ gốc, price_basis và expires_at; contract đầu vào nằm trong hướng dẫn bàn giao. `match_status` nên phân biệt ít nhất ghép đã xác nhận, chờ duyệt và bị từ chối. Dữ liệu giá chỉ để minh họa cấu trúc, không phải dữ liệu thị trường đo được.

## Nguyên tắc kiểm tra

- Từ chối hoặc cách ly dòng thiếu SKU/sản phẩm, giá, tiền tệ hoặc thời điểm quan sát bắt buộc.
- Không so sánh khác biến thể, tiền tệ hoặc điều kiện bán như thể tương đương.
- MVP ưu tiên VND. Nếu bật quy đổi, giữ giá gốc và ghi tỷ giá cùng ngày hiệu lực.
- SKU ghép chưa chắc chắn không tham gia so sánh cho đến khi Analyst xác nhận.
- Observation ID ổn định theo tenant, nguồn, SKU, `observed_at`, `price_basis` và giá VND. Retry cùng job hay tải lại cùng dữ liệu bằng job mới đều không tạo quan sát trùng. Cùng SKU và thời điểm nhưng giá khác là hai quan sát riêng; muốn sửa giá thì dùng `observed_at` mới.
- Không diễn giải thiếu dữ liệu tồn kho là “hết hàng”.
- Báo cáo cần công bố dữ liệu nguồn thiếu/cũ và khoảng thời gian tổng hợp.

## Hướng thiết kế khóa/truy vấn

Theo mục 2.2 của báo cáo đã nộp, giữ thiết kế dự kiến sau:

| Dữ liệu | Khóa và cách đọc |
| --- | --- |
| Catalog | Theo tenant_id và product_id; SKU/biến thể được xác nhận trong catalog của tenant. |
| Unified_Products | PK = `T#tenant_id#P#product_id`; SK = `source_id#observed_at#observation_id`. |
| GSI theo ngày | PK = `T#tenant_id#DAY#YYYYMMDD`; SK = `observed_at#product_id#source_id#observation_id`. Đọc các ngày UTC giao cửa sổ báo cáo 24 giờ. |
| Market_Summaries và AI_Intelligence_Reports | Khóa tenant và ngày, cùng report_id và kỳ dữ liệu cho số liệu/nhận định. |

Code dùng pk/sk và gsi1pk/gsi1sk theo mô hình trên, thời gian UTC có 6 chữ số phần giây. Hai bảng đã được triển khai trong stack `marketdata-hoang-dev`; kiểm thử CSV trên AWS ngày 01/11/2026 xác nhận tenant A có 90 quan sát, tenant B có 0 và phát lại không tăng dữ liệu. API phân trang/cursor vẫn cần Hiếu hoàn thiện. GSI có độ trễ đồng bộ; lịch sử tối đa 30 ngày phải được kiểm tra ở truy vấn, không chỉ dựa vào cơ chế xóa nền.

Job thu thập theo lịch mang tenant_id, source_id, scheduled_at và job_id ổn định qua retry; Dispatcher tạo metadata từ cấu hình phía máy chủ. Worker phải đối chiếu quyền sở hữu source/job trước khi xử lý. Normalizer ghi có điều kiện để cùng observation không tạo bản ghi mới khi gửi lại. TransformQueue hiện nhận envelope JSON tối đa 32 KiB mỗi dòng, chưa hỗ trợ tham chiếu S3 cho bản ghi lớn. Phía gửi kiểm tra `Failed` trong kết quả SendMessageBatch và báo lỗi để job được thử lại; các dòng đã gửi thành công có thể được gửi lại nhưng không tạo thêm quan sát.

Từ bản hợp nhất 04/11, receipt dùng ID theo job/dòng để mỗi job hoàn tất bộ đếm riêng; observation dùng ID theo nội dung để hai job nhập lại cùng giá không tạo bản ghi quan sát trùng. Dữ liệu cũ cần xem dry-run của `scripts.migrate_observation_ids` theo từng tenant trước khi quyết định migration; đồng bộ Git không tự chạy bước này.
