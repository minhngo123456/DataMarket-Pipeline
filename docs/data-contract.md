# Hợp đồng dữ liệu khởi đầu

> Các tên dưới đây chuẩn hóa những khái niệm trong báo cáo để thảo luận. Đây chưa phải JSON Schema hoặc schema triển khai; cần chốt phiên bản trước khi các worker tích hợp.

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

Trường và giá trị trong ví dụ chỉ là đề xuất; tên, kiểu và version cần được thống nhất. `match_status` nên phân biệt ít nhất ghép đã xác nhận, chờ duyệt và bị từ chối. Dữ liệu giá chỉ để minh họa cấu trúc, không phải dữ liệu thị trường đo được.

## Nguyên tắc kiểm tra

- Từ chối hoặc cách ly dòng thiếu SKU/sản phẩm, giá, tiền tệ hoặc thời điểm quan sát bắt buộc.
- Không so sánh khác biến thể, tiền tệ hoặc điều kiện bán như thể tương đương.
- MVP ưu tiên VND. Nếu bật quy đổi, giữ giá gốc và ghi tỷ giá cùng ngày hiệu lực.
- SKU ghép chưa chắc chắn không tham gia so sánh cho đến khi Analyst xác nhận.
- Dùng observation ID ổn định để retry cùng job không tạo quan sát trùng.
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

Đây là thiết kế, chưa phải schema đã triển khai. Cần chốt tên thuộc tính khóa, phân trang và cách biểu diễn thời gian trước khi code; không tự thay đổi mô hình truy vấn đã báo cáo. GSI có độ trễ đồng bộ; báo cáo lưu thời điểm chốt dữ liệu và số nguồn thành công. Lịch sử tối đa 30 ngày phải được kiểm tra ở truy vấn, không chỉ dựa vào cơ chế xóa nền.

Job thu thập theo lịch mang tenant_id, source_id, scheduled_at và job_id ổn định qua retry; Dispatcher tạo metadata từ cấu hình phía máy chủ. Worker phải đối chiếu quyền sở hữu source/job trước khi xử lý. Normalizer ghi có điều kiện để cùng observation không tạo bản ghi mới khi gửi lại. Bản ghi lớn truyền tham chiếu S3 qua TransformQueue; phía gửi kiểm tra từng kết quả SendMessageBatch và chỉ thử lại phần thất bại.
