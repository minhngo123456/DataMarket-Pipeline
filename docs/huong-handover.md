# Bàn giao công việc của Hướng (WBS 4 - Ingestion Workers)

> **Tác giả:** Phạm Văn Hướng  
> **Hạng mục:** WBS 4: CSV, API, web tĩnh (F2; 3 extractor và trạng thái nguồn - 64 giờ theo [team-work-split.md](team-work-split.md))  
> **Trạng thái:** API/Web worker, retry có backoff và cấu hình consumer SAM đã có. CSV worker là adapter tham chiếu do Hoàng thêm; Hướng phối hợp tích hợp và kiểm thử luồng chung. Toàn bộ kiểm thử local đạt; template chưa deploy lên AWS thật.

---

## 1. Các thành phần đã hoàn thành

| Thành phần | File mã nguồn | Mô tả chi tiết |
| --- | --- | --- |
| **CSV Extractor (phối hợp)** | `services/ingestion/csv_worker.py` | Adapter CSV tham chiếu do Hoàng thêm trước phần API/Web; tích hợp theo cùng envelope và TransformQueue. |
| **REST API Extractor** | `services/ingestion/api_worker.py` | Tiêu thụ job từ `APIQueue`. Gọi REST API đối tác, bóc tách JSON và kiểm tra trường bắt buộc. Retry lỗi 429/408/5xx/timeout có backoff và `Retry-After`; lỗi xác thực/payload sai được đánh dấu job thất bại. |
| **HTML Static Web Extractor** | `services/ingestion/web_worker.py` | Tiêu thụ job từ `WebQueue`. Thu thập trang HTML tĩnh được phép, bóc tách bảng giá hoặc thẻ dữ liệu (`data-*`), kiểm tra trường bắt buộc và dùng bộ phân tích chuẩn `HTMLParser`. Lỗi tạm thời retry theo cùng chính sách SQS. |
| **Retry và SQS batch** | `services/ingestion/fetch_errors.py`, `services/ingestion/batch.py` | Phân loại lỗi tạm thời, backoff có giới hạn, tôn trọng `Retry-After`, partial batch failure và cập nhật job khi hết số lần thử. |
| **SAM consumer** | `infra/aws/template.yaml` | Khai báo APIWorker/WebWorker, event source mappings, biến môi trường và IAM giới hạn theo queue. Cấu hình chưa được deploy lên AWS thật. |
| **Demo local** | `scripts/demo_ingestion.py` | Chạy API/Web worker hai lần trên fixture, gửi qua Normalizer và xác nhận idempotency cùng cách ly tenant bằng Moto. |
| **Dữ liệu mẫu (Fixtures)** | `tests/fixtures/api_response.json`<br>`tests/fixtures/sample_page.html` | Cung cấp dữ liệu mẫu chuẩn cho phản hồi REST API đối tác và trang bảng giá HTML tĩnh của đối thủ. |
| **Kiểm thử tích hợp & lỗi** | `tests/test_api_web_workers.py`, `tests/test_sam.py` | Kiểm tra API/Web → Normalizer, retry/backoff, giới hạn lần thử, header HTML, tenant/payload và queue mapping/IAM. |
| **Tài liệu Ingestion** | `services/ingestion/README.md` | Cập nhật kiến trúc và trách nhiệm các worker trong phân hệ thu thập dữ liệu. |

---

## 2. Kết quả kiểm tra tại máy cá nhân

Từ thư mục gốc `DataMarket-Pipeline`, dùng môi trường ảo Python 3.12:

```powershell
.venv\Scripts\Activate.ps1
pytest -q
ruff check .
.venv\Scripts\python.exe -m scripts.demo_ingestion
```

**Kết quả đạt được:**
- `pytest`: **52 passed** (41 test nền tảng ban đầu và 11 test API/Web).
- `ruff check .`: **All checks passed!** (0 lỗi cú pháp hoặc style).
- `scripts.validate_template`: SAM dịch offline thành 62 tài nguyên; `cfn-lint` đạt cho template nguồn và CloudFormation đã dịch.
- `scripts.demo_ingestion`: API và Web đều có 3 dòng accepted; chạy worker hai lần vẫn chỉ có 6 quan sát ở tenant A và 0 ở tenant B.

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
  - `"queued"`: Dispatcher vừa đưa job vào hàng đợi hoặc job đang chờ retry lỗi nguồn tạm thời.
  - `"normalizing"`: Extractor đã thu thập thành công và đang chuyển dòng cho Normalizer.
  - `"failed"`: Nguồn gặp lỗi không thể retry hoặc đã hết 5 lần nhận. Mã lỗi chi tiết được ghi trong trường `error` của Job; queue chuyển message hết lượt sang DLQ.
