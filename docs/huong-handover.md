# Bàn giao công việc của Hướng (WBS 4 - Ingestion Workers)

> **Tác giả:** Phạm Văn Hướng  
> **Hạng mục:** WBS 4: CSV, API, web tĩnh (F2; 3 extractor và trạng thái nguồn - 64 giờ theo [team-work-split.md](team-work-split.md))  
> **Trạng thái:** API/Web worker, retry/snapshot, trạng thái thu thập nguồn phía worker và cấu hình consumer SAM đã có. Luồng tạo nguồn qua API → Dispatcher/SQS → worker → Normalizer → API job/ma trận đã được kiểm thử local; kết quả hiện tại và lịch sử bộ đầu tiên ở mục 2. API/UI hiển thị trạng thái nguồn cần tích hợp theo hợp đồng bàn giao. CSV worker là adapter tham chiếu do Hoàng thêm; Hướng phối hợp tích hợp và kiểm thử luồng chung. Tài liệu này không xác nhận đã kiểm thử triển khai AWS thật.

---

## 1. Các thành phần đã hoàn thành

| Thành phần | File mã nguồn | Mô tả chi tiết |
| --- | --- | --- |
| **CSV Extractor (phối hợp)** | `services/ingestion/csv_worker.py` | Adapter CSV tham chiếu do Hoàng thêm trước phần API/Web; tích hợp theo cùng envelope và TransformQueue. |
| **REST API Extractor** | `services/ingestion/api_worker.py` | Tiêu thụ job từ `APIQueue`. Gọi REST API đối tác, bóc tách JSON và kiểm tra trường bắt buộc. Retry lỗi 429/408/5xx/timeout có backoff và `Retry-After`; lỗi xác thực/payload sai được đánh dấu job thất bại. |
| **HTML Static Web Extractor** | `services/ingestion/web_worker.py` | Tiêu thụ job từ `WebQueue`. Thu thập trang HTML tĩnh được phép, bóc tách bảng giá hoặc thẻ dữ liệu (`data-*`), kiểm tra trường bắt buộc và dùng bộ phân tích chuẩn `HTMLParser`. Lỗi tạm thời retry theo cùng chính sách SQS. |
| **Retry và SQS batch** | `services/ingestion/fetch_errors.py`, `services/ingestion/batch.py` | Phân loại lỗi tạm thời, backoff có giới hạn, tôn trọng `Retry-After`, partial batch failure và cập nhật job khi hết số lần thử. |
| **Trạng thái thu thập nguồn** | `marketdata/repository.py`, `services/ingestion/collection.py`, `tests/test_source_collection.py` | Worker ghi trạng thái nguồn và lịch sử enqueue thành công, giữ mốc thành công khi retry và chặn job cũ ghi đè job mới. API/UI tích hợp theo [hợp đồng bàn giao](source-collection-status.md). |
| **SAM consumer** | `infra/aws/template.yaml` | Khai báo APIWorker/WebWorker, event source mappings, biến môi trường và IAM giới hạn theo queue. Cấu hình chưa được deploy lên AWS thật. |
| **Demo local** | `scripts/demo_ingestion.py` | Chạy API/Web worker hai lần trên fixture, gửi qua Normalizer và xác nhận idempotency cùng cách ly tenant bằng Moto. |
| **Luồng nguồn local qua API** | `scripts/demo_source_pipeline.py`, `tests/test_source_pipeline.py` | Tạo nguồn qua API handler, Dispatcher/SQS Moto → API/Web snapshot → Normalizer → đọc job và ma trận qua API; mô phỏng authorizer, vẫn kiểm membership/RBAC phía máy chủ. |
| **Dữ liệu mẫu (Fixtures)** | `tests/fixtures/api_response.json`<br>`tests/fixtures/sample_page.html` | Cung cấp dữ liệu mẫu chuẩn cho phản hồi REST API đối tác và trang bảng giá HTML tĩnh của đối thủ. |
| **Kiểm thử tích hợp & lỗi** | `tests/test_api_web_workers.py`, `tests/test_sam.py` | Kiểm tra API/Web → Normalizer, retry/backoff, giới hạn lần thử, header HTML, tenant/payload và queue mapping/IAM. |
| **Tài liệu Ingestion** | `services/ingestion/README.md` | Cập nhật kiến trúc và trách nhiệm các worker trong phân hệ thu thập dữ liệu. |

Phần chuẩn bị hạ tầng API/Web thuộc 8 giờ Hướng tham gia WBS 2 được đối chiếu tại [checklist chuẩn bị AWS của Hướng](huong-aws-readiness.md): SAM consumer, env/IAM, artifact offline và điều kiện trước khi kiểm thử AWS thật.

---

## 2. Kết quả kiểm tra tại máy cá nhân

Từ thư mục gốc `DataMarket-Pipeline`, dùng môi trường ảo đã cài `requirements-dev.lock.txt`:

```powershell
.venv\Scripts\Activate.ps1
pytest -q
ruff check .
.venv\Scripts\python.exe -m scripts.demo_ingestion
```

**Lịch sử kết quả của bộ đầu tiên, không phải số liệu của bộ kiểm thử hiện tại:**
- `pytest`: **52 passed** (41 test nền tảng ban đầu và 11 test API/Web).
- `ruff check .`: **All checks passed!** (0 lỗi cú pháp hoặc style).
- `scripts.validate_template`: SAM dịch offline thành 62 tài nguyên; `cfn-lint` đạt cho template nguồn và CloudFormation đã dịch.
- `scripts.demo_ingestion`: API và Web đều có 3 dòng accepted; chạy worker hai lần vẫn chỉ có 6 quan sát ở tenant A và 0 ở tenant B.

### Luồng nguồn local mới

```powershell
.venv\Scripts\python.exe -m scripts.demo_source_pipeline
.venv\Scripts\python.exe -m pytest tests/test_source_pipeline.py -q
```

Harness tạo catalog và nguồn bằng API handler (`POST /sources`), chạy Dispatcher theo thời điểm cố định, nhận message SQS Moto rồi xử lý API/Web → snapshot S3 Moto → TransformQueue → Normalizer. Kết quả được đọc qua `GET /jobs/{job_id}/status` và `GET /matrix` để đối chiếu giá, trạng thái, replay cùng kỳ lịch và cách ly tenant. Mapping SKU đã biết là fixture được seed sẵn. Bộ kiểm thử của harness nằm trong `tests/test_source_pipeline.py`.

**Kết quả local ngày 04/11/2026:**

- Toàn bộ `pytest -q -p no:cacheprovider`: **254 passed**, gồm **10 kiểm thử** trong `tests/test_source_pipeline.py`, **29 kiểm thử** trong `tests/test_source_collection.py` và hai kiểm thử bổ sung về CSV xử lý đồng thời/phục hồi health trong `tests/test_ingestion.py`.
- `ruff check --no-cache .`: đạt.
- Demo tạo hai nguồn qua API, Dispatcher tạo hai job; API và Web đều hoàn tất với **3 accepted, 0 pending, 0 rejected**.
- Ma trận tenant A có **6** dòng đúng giá fixture; tenant B không được cấu hình nguồn có **0** dòng. Replay cùng kỳ lịch giữ **6** receipts và chỉ **2** lượt fetch nguồn tổng cộng.
- Kiểm thử bao phủ PATCH cấu hình cho kỳ sau, tắt/xóa nguồn, hai tenant có cùng ID nhưng giá khác, membership/RBAC, lỗi nguồn độc lập, retry SQS nhận lần 1→2 và partial send sử dụng lại snapshot. Có kiểm tra chặn HTTP ngoài ý muốn và phục hồi cấu hình AWS của môi trường khi thoát harness.
- Trạng thái nguồn được kiểm tra trước khi Normalizer hoàn tất: chỉ succeeded khi đã enqueue đủ, giữ thành công trước khi retry/lỗi, không cho job cũ ghi đè job mới, che URL/token giả trong log và mã lỗi, retry đến lần nhận thứ 5. Có fault injection phục hồi ghi health/history, chặn lỗi muộn làm hỏng job đã enqueue đủ và chặn CSV delivery cũ mở lại job completed.
- SAM dịch offline thành **98 tài nguyên**; `cfn-lint` đạt cho nguồn và bản dịch. Đã staging và audit **41 file** runtime/requirements khớp nguồn; đây chưa phải gói đã build/cài dependency trên Python 3.12. Xem [bằng chứng chuẩn bị AWS](huong-aws-readiness.md).
- Runtime kiểm tra trên máy này là **Python 3.13.14**; cấu hình Lambda trong SAM vẫn là Python 3.12. Kết quả local không xác nhận runtime triển khai AWS.

Claims authorizer là giả lập, nhưng membership, tenant và vai trò được handler kiểm tra từ dữ liệu phía máy chủ. Harness dùng credential giả, feed theo allowlist fixture, chặn DNS/TCP và không in header/credential hoặc toàn bộ response cấu hình nguồn. Không gọi tài khoản AWS hay nguồn HTTP thật.

Đây là mô phỏng ứng dụng local, không xác minh Cognito/JWT thật, IAM, EventBridge/Scheduler, cơ chế vận chuyển sự kiện Lambda/SQS hoặc trình duyệt. Các phần kiểm thử triển khai cần ghi riêng; không coi Moto là bằng chứng AWS đã chạy. Tiếp tục tuân theo [điều kiện sử dụng AWS](huong-aws-readiness.md): mặc định Moto/local, dịch vụ bắt buộc có phí cần Hoàng duyệt trước và không công khai key/credential.

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
  - `"normalizing"`: Extractor đã ghi số dòng và bắt đầu chuyển cho Normalizer; có thể còn retry gửi SQS. Chỉ `collection_status="succeeded"` mới xác nhận extractor đã gửi đủ dòng.
  - `"failed"`: Nguồn gặp lỗi không thể retry hoặc đã hết 5 lần nhận. Mã lỗi chi tiết được ghi trong trường `error` của Job; queue chuyển message hết lượt sang DLQ.
