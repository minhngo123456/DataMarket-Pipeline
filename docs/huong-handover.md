# Bàn giao công việc của Hướng (WBS 4 - Ingestion Workers)

> **Tác giả:** Phạm Văn Hướng  
> **Hạng mục:** WBS 4: CSV, API, web tĩnh (F2; 3 extractor và trạng thái nguồn - 64 giờ theo [team-work-split.md](team-work-split.md))  
> **Trạng thái:** Đã hoàn thành triển khai mã nguồn, bộ bóc tách dữ liệu, fixtures và bộ kiểm thử tự động với 46/46 test passed.

---

## 1. Các thành phần đã hoàn thành

| Thành phần | File mã nguồn | Mô tả chi tiết |
| --- | --- | --- |
| **REST API Extractor** | `services/ingestion/api_worker.py` | Tiêu thụ job từ `APIQueue`. Gọi REST API đối tác, bóc tách JSON, kiểm tra 4 trường bắt buộc (`source_sku`, `price`, `currency`, `observed_at`), xử lý HTTP 429/403/timeout, chuyển trạng thái job và đẩy dữ liệu chuẩn vào `TransformQueue`. |
| **HTML Static Web Extractor** | `services/ingestion/web_worker.py` | Tiêu thụ job từ `WebQueue`. Thu thập trang HTML tĩnh được phép, bóc tách bảng giá (`<table>`) hoặc thẻ dữ liệu (`data-sku`, `data-price`), loại bỏ rủi ro phụ thuộc bên ngoài bằng bộ phân tích chuẩn `HTMLParser`. |
| **Dữ liệu mẫu (Fixtures)** | `tests/fixtures/api_response.json`<br>`tests/fixtures/sample_page.html` | Cung cấp dữ liệu mẫu chuẩn cho phản hồi REST API đối tác và trang bảng giá HTML tĩnh của đối thủ. |
| **Kiểm thử tích hợp & lỗi** | `tests/test_api_web_workers.py` | 5 bài test bao quát: luồng end-to-end từ worker sang Normalizer, xử lý lỗi kết nối/HTTP 429, xử lý HTML sai định dạng, chặn tiêm nhiễm `tenant_id`, và giới hạn dung lượng 1 MiB. |
| **Tài liệu Ingestion** | `services/ingestion/README.md` | Cập nhật kiến trúc và trách nhiệm các worker trong phân hệ thu thập dữ liệu. |

---

## 2. Kết quả kiểm tra tại máy cá nhân

Từ thư mục gốc `DataMarket-Pipeline`, kích hoạt môi trường ảo Python 3.12/3.13:

```powershell
.venv\Scripts\Activate.ps1
pytest -q
ruff check .
```

**Kết quả đạt được:**
- `pytest`: **46 passed** (41 test nền tảng của Hoàng + 5 test mở rộng cho API/Web worker).
- `ruff check .`: **All checks passed!** (0 lỗi cú pháp hoặc style).

---

## 3. Quy chuẩn hợp đồng dữ liệu cho các thành viên khác

### Dành cho Hoàng (Hạ tầng / Normalizer):
- Cả hai worker `api_worker` và `web_worker` đều sử dụng tiện ích `send_rows` của Hoàng để đẩy các dòng bản ghi vào `TransformQueue` theo lô (tối đa 10 tin nhắn và 240 KiB/lô).
- Cấu trúc mỗi bản ghi vào `TransformQueue` tuân thủ nghiêm ngặt hợp đồng:
  ```json
  {
    "schema_version": 1,
    "tenant_id": "tenant-a",
    "source_id": "api_supplier",
    "job_id": "job-id",
    "row_number": 1,
    "record": {
      "source_sku": "001-PHONE",
      "price": "20100000",
      "currency": "VND",
      "observed_at": "2026-10-19T00:45:00Z",
      "price_basis": "tax_included"
    }
  }
  ```
- Worker luôn cập nhật `row_count` của job trước khi đẩy dữ liệu vào `TransformQueue`, đảm bảo Normalizer có thể tính toán chính xác tỷ lệ hoàn thành (`refresh_job`).

### Dành cho Hiếu (API / Quản lý nguồn):
- Khi tạo nguồn API hoặc Web trong DynamoDB (`SOURCE#<source_id>`):
  - Nguồn API cần có trường `endpoint` (URL gọi API) và tùy chọn `headers` (được bảo mật hoặc chuyển tiếp).
  - Nguồn Web cần có trường `url` (địa chỉ trang HTML tĩnh được phép thu thập).
  - Trường `enabled: true` để cho phép `dispatcher.py` kích hoạt job theo lịch 6 giờ.

### Dành cho Minh (Dashboard / UI):
- Trạng thái của job thu thập:
  - `"queued"`: Dispatcher vừa đưa job vào hàng đợi.
  - `"normalizing"`: Extractor đã thu thập thành công và đang chuyển dòng cho Normalizer.
  - `"failed"`: Nguồn gặp lỗi (sai URL, lỗi kết nối, HTTP 429, hoặc thiếu trường bắt buộc). Mã lỗi chi tiết được ghi trong trường `error` của Job.
