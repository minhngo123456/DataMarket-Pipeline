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

Bản thiết kế đề xuất khóa tenant-scoped cho sản phẩm/lịch sử và một index theo ngày để đọc theo cửa sổ thời gian. Cần chốt partition/sort key, phân trang và độ trễ đồng bộ của index sau khi xác định truy vấn báo cáo; kiểm tra với giới hạn Pilot 100 SKU trước khi triển khai.
