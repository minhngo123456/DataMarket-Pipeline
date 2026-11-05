# API Contract - Frontend ↔ Backend

> **Dự án:** DataMarket-Pipeline  
> **Các bên phụ trách:** Ngô Nguyễn Nhật Minh (Frontend & UX) ↔ Đỗ Huy Hiếu (Auth, API & AI Report)  
> **Phiên bản:** 1.0 (Chốt theo kế hoạch MVP)  
> **Tiêu chuẩn giao tiếp:** RESTful API qua AWS API Gateway HTTP API, định dạng JSON UTF-8.

---

## 1. Quy chuẩn Chung (Global Conventions)

### 1.1. Xác thực (Authentication)
Mọi request từ Frontend bắt buộc đính kèm JWT ID Token nhận được từ AWS Cognito:
```http
Authorization: Bearer {cognito_id_token}
```
*Ghi chú môi trường Test/Dev:* Khi chạy mock cục bộ không qua Cognito thật, có thể dùng header:
```http
X-Test-Sub: analyst-a
```

### 1.2. Định dạng Phản hồi Lỗi (Error Response)
Tất cả các lỗi trả về HTTP Status tương ứng kèm body chuẩn:
```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "Chi tiết lý do lỗi (tiếng Việt hoặc mã lỗi chuẩn)"
  }
}
```
Các mã lỗi phổ biến:
- `UNAUTHENTICATED` (401): Thiếu hoặc sai token.
- `FORBIDDEN` / `ACCESS_DENIED` (403): Người dùng bị vô hiệu hóa hoặc không đủ vai trò (role).
- `NOT_FOUND` (404): Không tìm thấy tài nguyên trong phạm vi tenant.
- `CONFLICT` (409): Xung đột dữ liệu (ví dụ SKU đã được duyệt bởi người khác).
- `QUOTA_EXCEEDED` (429): Vượt quá hạn mức gói Pilot (100 SKU, 3 nguồn, 5 thành viên, 1 báo cáo/ngày).
- `INVALID_REQUEST` (400): Tham số payload không hợp lệ.

### 1.3. Định dạng Phân trang (Pagination)
Các danh sách dạng bảng (Ma trận, Sản phẩm, Báo cáo, Nguồn) trả về bọc trong cấu trúc:
```json
{
  "data": [ ... ],
  "total": 100,
  "page": 1,
  "limit": 20
}
```

### 1.4. Phân quyền Người dùng (RBAC)
- **Admin**: Quản lý toàn quyền (nguồn, thành viên, catalog, upload, duyệt SKU, xem báo cáo).
- **Analyst**: Quản lý nghiệp vụ (thêm nguồn, upload CSV, duyệt ghép SKU, xem ma trận & báo cáo).
- **Viewer**: Chỉ đọc (xem dashboard, ma trận, danh mục, lịch sử giá và báo cáo).

---

## 2. Danh mục 18 Endpoints Chi tiết

| # | Method | Path | Mô tả | Role | Tuần cần |
|---|---|---|---|---|:---:|
| 1 | `GET` | `/me` | Thông tin người dùng + tenant + vai trò | All | 2 |
| 2 | `GET` | `/dashboard/stats` | Tổng SKU, số nguồn, trạng thái lỗi, report mới nhất | All | 2 |
| 3 | `GET` | `/alerts` | Danh sách cảnh báo giá/nguồn bất thường | All | 2 |
| 4 | `GET` | `/sources` | Danh sách nguồn dữ liệu + trạng thái hoạt động | All | 2 |
| 5 | `POST` | `/sources` | Tạo nguồn hoặc thay cấu hình nguồn cùng loại (Tối đa 3 nguồn) | Admin, Analyst | 3 |
| 6 | `PATCH` | `/sources/:id` | Sửa cấu hình / Bật / Tắt nguồn | Admin, Analyst | 3 |
| 7 | `DELETE` | `/sources/:id` | Xóa nguồn dữ liệu | Admin | 3 |
| 8 | `POST` | `/jobs` | Tạo job upload $\rightarrow$ Nhận S3 presigned PUT URL | Admin, Analyst | 3 |
| 9 | `GET` | `/jobs/:id/status` | Kiểm tra trạng thái xử lý của Job upload | Admin, Analyst | 3 |
| 10 | `GET` | `/matrix` | Ma trận so sánh giá 24 giờ (Paginated) | All | 3 |
| 11 | `GET` | `/products` | Danh sách SKU + trạng thái ghép (Filterable) | All | 3 |
| 12 | `PATCH` | `/products/:id/match` | Xác nhận duyệt hoặc từ chối ghép SKU | Admin, Analyst | 3 |
| 13 | `GET` | `/products/:id/price-history` | Lịch sử biến động giá 30 ngày của sản phẩm | All | 4 |
| 14 | `GET` | `/reports` | Danh sách các báo cáo phân tích thị trường | All | 4 |
| 15 | `GET` | `/reports/:id` | Chi tiết báo cáo + Nội dung tóm tắt từ Claude AI | All | 4 |
| 16 | `GET` | `/members` | Danh sách thành viên trong tenant (Tối đa 5 user) | Admin | 4 |
| 17 | `POST` | `/members/invite` | Thêm / Mời thành viên mới vào tenant | Admin | 4 |
| 18 | `PATCH` | `/members/:id` | Cập nhật vai trò (Admin đổi role) hoặc trạng thái thành viên | Admin | 4 |

---

## 3. Đặc tả Chi tiết Request / Response Từng Endpoint

### Nhóm 1: Xác thực & Tổng quan Dashboard (Tuần 2)

#### 1. `GET /me`
Lấy định danh, vai trò và thông tin tenant hiện tại.
- **Role:** All (Admin, Analyst, Viewer)
- **Response 200:**
```json
{
  "user_id": "us-east-1_abc123",
  "tenant_id": "tenant-a",
  "role": "analyst",
  "tenant": {
    "name": "Công ty Bán lẻ A",
    "status": "active",
    "product_count": 42,
    "max_skus": 100,
    "max_sources": 3,
    "max_members": 5
  },
  "claims": {
    "email": "analyst@example.com",
    "sub": "us-east-1_abc123"
  }
}
```

---

#### 2. `GET /dashboard/stats`
Dữ liệu số liệu thống kê tổng hợp cho các thẻ KPI trên màn hình chính Dashboard.
- **Role:** All
- **Response 200:**
```json
{
  "total_skus": 42,
  "max_skus": 100,
  "total_sources": 3,
  "active_sources": 2,
  "pending_skus": 5,
  "error_count_24h": 0,
  "latest_report": {
    "report_id": "rep_20261021",
    "date": "2026-10-21",
    "status": "ready",
    "ai_status": "ai_generated",
    "summary_preview": "Thị trường ghi nhận 3 mặt hàng điện tử có biến động giá giảm..."
  }
}
```

---

#### 3. `GET /alerts`
Danh sách các cảnh báo cần người dùng lưu ý (giá chênh lệch bất thường > 15%, nguồn quá hạn không gửi dữ liệu, SKU chờ duyệt).
- **Role:** All
- **Query Params:** `?page=1&limit=20&type=price_anomaly` (tùy chọn)
- **Response 200:**
```json
{
  "data": [
    {
      "id": "alert-001",
      "type": "price_anomaly",
      "severity": "warning",
      "title": "Chênh lệch giá bất thường ở iPhone 15",
      "message": "Nguồn đối thủ Tiki có giá thấp hơn 18.5% so với giá nội bộ.",
      "product_id": "iphone-15",
      "source_id": "tiki",
      "created_at": "2026-10-21T02:15:00Z"
    },
    {
      "id": "alert-002",
      "type": "pending_sku",
      "severity": "info",
      "title": "SKU mới cần xác nhận ghép",
      "message": "SKU 'IP15-128-BLK' từ CSV chưa có trong catalog.",
      "created_at": "2026-10-21T01:30:00Z"
    }
  ],
  "total": 2,
  "page": 1,
  "limit": 20
}
```

---

#### 4. `GET /sources`
Lấy danh sách các nguồn dữ liệu đã cấu hình của tenant.
- **Role:** All
- **Response 200:**
```json
{
  "data": [
    {
      "source_id": "vendor",
      "name": "Nhà cung cấp Vendor CSV",
      "kind": "csv",
      "enabled": true,
      "updated_at": "2026-10-20T10:00:00Z"
    },
    {
      "source_id": "competitor-api",
      "name": "API Đối thủ A",
      "kind": "api",
      "endpoint": "https://api.competitor-a.com/v1/prices",
      "url": "https://api.competitor-a.com/v1/prices",
      "headers": {
        "Authorization": "***"
      },
      "enabled": true,
      "updated_at": "2026-10-20T12:00:00Z",
      "collection_status": "retrying",
      "last_job_id": "job-api-20261020-1200",
      "last_attempt_at": "2026-10-20T12:00:00Z",
      "last_success_at": "2026-10-20T06:00:05Z",
      "last_success_job_id": "job-api-20261020-0600",
      "last_success_job_at": "2026-10-20T06:00:00Z",
      "last_error_code": "api_http_retryable_429"
    },
    {
      "source_id": "web-competitor-b",
      "name": "Website Đối thủ B",
      "kind": "web",
      "url": "https://shop.competitor-b.vn/bang-gia",
      "endpoint": "https://shop.competitor-b.vn/bang-gia",
      "enabled": true,
      "updated_at": "2026-10-20T12:30:00Z"
    }
  ],
  "total": 3,
  "page": 1,
  "limit": 20
}
```
*Ghi chú:* giá trị trong `headers` chỉ trả nguyên văn cho Admin. Analyst và Viewer chỉ thấy tên header, giá trị là `***` như ví dụ trên. Nếu `POST /sources` hoặc `PATCH /sources/:id` gửi lại `***` cho một header, hệ thống giữ giá trị đã lưu của header đó; header chưa có giá trị thì bị bỏ qua.

Các thay đổi `/sources` dưới đây đã được chuẩn bị trong code local; chưa xác nhận UI tích hợp hoặc kiểm thử AWS thật. Response hiện còn có `sources` là alias của cùng danh sách `data`.

| Trường health công khai, chỉ có khi server đã lưu giá trị | Ý nghĩa |
| --- | --- |
| `collection_status` | `running`, `retrying`, `succeeded` hoặc `failed`. Chưa có thì UI hiển thị “Chưa thu thập”. |
| `last_job_id` | Job của lần thu thập hiện tại. |
| `last_attempt_at` | Lần bắt đầu xử lý đầu tiên của job, UTC ISO 8601; không đổi qua retry cùng job. |
| `last_success_at` | Mốc server xác nhận enqueue thành công; giữ lại khi job sau lỗi. |
| `last_success_job_id` | Job được ghi nhận thành công gần nhất. |
| `last_success_job_at` | Thời điểm logic của job thành công đó. |
| `last_error_code` | Mã lỗi an toàn khi retry/failed; không chứa URL/header hoặc credential. |

Trường chưa có hoặc có giá trị null được bỏ khỏi response. `collection_status="succeeded"` chỉ xác nhận extractor đã gửi đủ dòng sang TransformQueue; không chứng minh Normalizer đã accepted mọi dòng. Đối chiếu riêng trạng thái job và accepted/pending/rejected khi hiển thị kết quả chuẩn hóa.

Client không được tự ghi health. `source_config_revision`, `collection_job_key`, `collection_job_at` và `collection_success_key` là các trường nội bộ, không xuất hiện trong response nguồn. Xem thêm [hợp đồng source health](source-collection-status.md).

---

### Nhóm 2: Nguồn, Jobs & Upload CSV, Ma trận Giá (Tuần 3)

#### 5. `POST /sources`
Tạo nguồn dữ liệu mới, hoặc thay cấu hình được hỗ trợ của nguồn đã tồn tại cùng `source_id` và cùng `kind`. Giới hạn tối đa 3 nguồn vẫn áp dụng khi thêm nguồn mới; cập nhật nguồn cùng ID không tạo thêm nguồn.
- **Role:** Admin, Analyst
- **Request Body (Nguồn API hoặc Web):**
```json
{
  "source_id": "competitor-api",
  "name": "API Đối thủ A",
  "kind": "api",
  "endpoint": "https://api.competitor-a.com/v1/prices",
  "headers": {
    "Authorization": "Bearer secret-token-xyz"
  },
  "enabled": true
}
```
*Ghi chú:*
- Với `kind` là `api` hoặc `web`, hỗ trợ truyền `endpoint` hoặc `url` để crawler/worker tự động quét dữ liệu. Hệ thống lưu đồng thời cả `endpoint` và `url` để tương thích hoàn toàn với cả `api_worker` và `web_worker`.
- `headers` là JSON object (tùy chọn) chứa header truyền kèm khi gửi HTTP request (User-Agent, API Key, Authorization,...).
- Với `kind` là `csv`, không yêu cầu `endpoint`/`url`.
- `endpoint`/`url` phải là URL `http(s)`; giá trị khác trả `400 invalid_source_payload` (áp dụng cả cho `PATCH /sources/:id`).
- Worker chỉ kết nối tới địa chỉ công khai. URL trỏ tới loopback, mạng riêng, link-local, metadata, multicast hoặc dải dành riêng (kể cả qua redirect hay DNS) làm job `failed` với `source_destination_blocked`.
- Khi redirect sang host khác, worker bỏ mọi header đã cấu hình trừ `User-Agent`.
- POST cùng kind giữ nguyên health do worker ghi nhưng thay các trường cấu hình được hỗ trợ: `name` mặc định là source ID, `enabled` mặc định true; không có URL qua `endpoint`/`url` thì xóa cả hai trường; `headers` thiếu hoặc null thì xóa cấu hình header. POST vẫn trả 201 trong trường hợp này. Dùng PATCH nếu chỉ muốn sửa một phần.
- Health, tenant và revision nội bộ không được lấy từ body. Server dùng UUID revision nội bộ và cập nhật có điều kiện để không ghi đè cấu hình đã đổi sau lần đọc hoặc tạo lại nguồn vừa bị xóa. Client không cần gửi revision.
- Giá trị header trong response vẫn tuân theo role: Admin đọc giá trị gốc, Analyst nhận `***`.

- **Response 201:**
```json
{
  "source_id": "competitor-api",
  "name": "API Đối thủ A",
  "kind": "api",
  "endpoint": "https://api.competitor-a.com/v1/prices",
  "url": "https://api.competitor-a.com/v1/prices",
  "headers": {
    "Authorization": "Bearer secret-token-xyz"
  },
  "enabled": true,
  "status": "configured"
}
```
- **Error 409:** `error.code` là `conflict`, `error.message` phân biệt:
  - `source_kind_conflict`: source ID đã tồn tại với kind khác; không tự đổi kind hoặc xóa health.
  - `source_configuration_changed`: cấu hình đã bị thay đổi/xóa/tạo lại sau lần server đọc. Client tải lại `GET /sources`, đối chiếu dữ liệu rồi thực hiện lại thay đổi phù hợp; không tự gửi lặp request cũ vô hạn.

- **Error 429 (Khi thêm nguồn mới vượt hạn mức):**
```json
{
  "error": {
    "code": "quota_exceeded",
    "message": "source_limit_reached"
  }
}
```

---

#### 6. `PATCH /sources/:id`
Bật/tắt, sửa tên hoặc cập nhật endpoint, url, headers cho nguồn dữ liệu.
- **Role:** Admin, Analyst
- Chỉ cập nhật các trường cấu hình được hỗ trợ, giữ health và cấu hình không có trong request; `headers:null` xóa cấu hình header. Không hỗ trợ đổi kind qua PATCH.
- POST/PATCH dùng UUID revision do server quản lý và `UpdateItem` có điều kiện; worker ghi health không làm thay revision cấu hình. Request PATCH cũ không được tạo lại source đã bị xóa hoặc ghi đè source/cấu hình vừa thay đổi. Revision và health không được nhận từ client.
- **Request Body:**
```json
{
  "name": "API Đối thủ A (v2)",
  "endpoint": "https://api.competitor-a.com/v2/prices",
  "headers": {
    "Authorization": "Bearer new-token"
  },
  "enabled": true
}
```
- **Response 200:**
```json
{
  "source_id": "competitor-api",
  "name": "API Đối thủ A (v2)",
  "endpoint": "https://api.competitor-a.com/v2/prices",
  "url": "https://api.competitor-a.com/v2/prices",
  "headers": {
    "Authorization": "Bearer new-token"
  },
  "enabled": true,
  "status": "updated"
}
```

- **Error 409:** `{"error":{"code":"conflict","message":"source_configuration_changed"}}`. Tải lại `GET /sources` trước khi sửa tiếp; server không áp dụng lại request cũ lên nguồn đã thay đổi.
- **Error 404:** nguồn chưa tồn tại khi server đọc trả `source_not_found`; PATCH không tạo nguồn mới.
- Response vẫn che header cho Analyst theo quy tắc của GET; không trả các trường nội bộ hoặc revision.

**Giới hạn snapshot và vòng đời nguồn:** snapshot chỉ cố định sau khi capture/lưu dữ liệu. Job đã có snapshot retry trên dữ liệu cũ; job chưa có snapshot có thể fetch URL/header cấu hình mới. Revision cấu hình chưa ngăn job cũ ghi health vào nguồn bị xóa rồi tạo lại cùng source ID/cùng kind; việc gắn job với source generation riêng còn cần xử lý.

---

#### 7. `DELETE /sources/:id`
Xóa cấu hình một nguồn dữ liệu.
- **Role:** Admin
- DELETE hiện chưa kiểm tra revision khi xóa. Request DELETE cũ vẫn có thể xóa nguồn đã thay đổi/tạo lại sau lần server đọc; không coi cơ chế revision POST/PATCH là đã bảo vệ DELETE hoặc toàn bộ vòng đời source.
- **Response 200:**
```json
{
  "source_id": "web-competitor-b",
  "status": "deleted"
}
```

---

#### 8. `POST /jobs`
Tạo job upload file CSV và nhận Presigned S3 PUT URL để trình duyệt tải trực tiếp lên AWS S3 RawBucket.
- **Role:** Admin, Analyst
- **Request Body:**
```json
{
  "source_id": "vendor",
  "filename": "bang_gia_ngay_21.csv",
  "size_bytes": 20480
}
```
- `size_bytes` là tùy chọn. Nếu vượt quá 1 MiB (1.048.576 byte), API trả `413 csv_too_large` và không tạo job. CSVWorker vẫn tự kiểm tra giới hạn 1 MiB và 1.000 dòng.
- **Response 201:**
```json
{
  "job_id": "job_a1b2c3d4",
  "source_id": "vendor",
  "s3_key": "tenants/tenant-a/raw/job_a1b2c3d4",
  "upload_url": "https://raw-bucket.s3.amazonaws.com/tenants/tenant-a/raw/job_a1b2c3d4?AWSAccessKeyId=...",
  "expires_in_seconds": 900
}
```
*Frontend sử dụng link `upload_url` này để thực hiện `PUT` file CSV bằng `fetch()` hoặc `axios` mà không qua backend trung gian.*

---

#### 9. `GET /jobs/:id/status`
Kiểm tra tiến độ xử lý file CSV sau khi tải lên.
- **Role:** Admin, Analyst
- **Response 200:**
```json
{
  "job_id": "job_a1b2c3d4",
  "status": "completed",
  "row_count": 100,
  "counts": {
    "accepted": 92,
    "pending": 5,
    "rejected": 3
  },
  "created_at": "2026-10-21T01:00:00Z"
}
```
*Các trạng thái `status`: `queued`, `extracting`, `normalizing`, `completed`.*

---

#### 10. `GET /matrix`
Bảng ma trận so sánh giá 24 giờ giữa giá tham chiếu nội bộ (`internal`) và các nguồn đối thủ.
- **Role:** All
- **Query Params:**
  - `?page=1&limit=20` (phân trang)
  - `?reference_source=internal` (mặc định)
  - `?basis=tax_included` (mặc định)
- **Response 200:**
```json
{
  "data": [
    {
      "product_id": "iphone-15",
      "product_name": "iPhone 15 128GB",
      "reference_price": 20000000,
      "source_id": "competitor-a",
      "price": 19500000,
      "currency": "VND",
      "change_percent": -2.5,
      "observed_at": "2026-10-21T06:30:00Z"
    }
  ],
  "window_start": "2026-10-20T07:00:00Z",
  "window_end": "2026-10-21T07:00:00Z",
  "total": 42,
  "page": 1,
  "limit": 20
}
```

---

#### 11. `GET /products`
Lấy danh sách catalog sản phẩm, có thể lọc theo trạng thái ghép (`match_status`).
- **Role:** All
- **Query Params:** `?page=1&limit=20&match_status=all` (`confirmed`, `pending`, `rejected`)
- **Response 200:**
```json
{
  "data": [
    {
      "product_id": "phone-01",
      "name": "Samsung Galaxy S24",
      "attributes": { "color": "Gray", "storage": "256GB" },
      "match_status": "confirmed",
      "matched_sku": "001-S24"
    },
    {
      "observation_id": "obs_999",
      "product_id": null,
      "source_sku": "S24-UNKNOWN-SKU",
      "price": 18500000,
      "currency": "VND",
      "match_status": "pending",
      "source_id": "vendor",
      "reason": "unmapped_sku"
    }
  ],
  "total": 45,
  "page": 1,
  "limit": 20
}
```

---

#### 12. `PATCH /products/:id/match`
Xác nhận duyệt ghép một SKU chưa rõ vào một sản phẩm trong Catalog (hoặc từ chối).
- **Role:** Admin, Analyst
- **Request Body:**
```json
{
  "observation_id": "obs_999",
  "product_id": "phone-01",
  "action": "approve"
}
```
- **Response 200:**
```json
{
  "observation_id": "obs_999",
  "product_id": "phone-01",
  "status": "approved",
  "message": "Ghép SKU thành công, quy tắc mapping đã được ghi nhận cho các lần import sau."
}
```
- **Error 409 (Nếu đã có người khác duyệt trước):**
```json
{
  "error": {
    "code": "CONFLICT",
    "message": "Dòng dữ liệu này đã được duyệt bởi phiên làm việc khác."
  }
}
```

---

### Nhóm 3: Lịch sử Giá, Báo cáo AI & Quản trị Thành viên (Tuần 4)

#### 13. `GET /products/:id/price-history`
Lấy chuỗi lịch sử quan sát giá của 1 sản phẩm cụ thể trong tối đa 30 ngày (dùng để vẽ biểu đồ Line Chart).
- **Role:** All
- **Query Params:** `?days=30` (mặc định 30, từ 1 đến 30)
- **Response 200:**
```json
{
  "product_id": "phone-01",
  "days": 30,
  "data": [
    {
      "observation_id": "obs_101",
      "source_id": "vendor",
      "price": 18500000,
      "currency": "VND",
      "price_basis": "tax_included",
      "observed_at": "2026-10-15T02:00:00Z"
    },
    {
      "observation_id": "obs_102",
      "source_id": "competitor-a",
      "price": 18200000,
      "currency": "VND",
      "price_basis": "tax_included",
      "observed_at": "2026-10-18T05:00:00Z"
    }
  ],
  "total": 2
}
```

---

#### 14. `GET /reports`
Lấy danh sách các báo cáo thị trường hằng ngày đã được sinh ra.
- **Role:** All
- **Query Params:** `?page=1&limit=20`
- **Response 200:**
```json
{
  "data": [
    {
      "report_id": "rep_20261021",
      "period": "2026-10-21",
      "status": "ready",
      "ai_status": "ai_generated",
      "summary": "Thị trường ghi nhận mức giảm 3.5% ở phân khúc điện thoại...",
      "created_at": "2026-10-21T01:00:05Z"
    }
  ],
  "total": 1,
  "page": 1,
  "limit": 20
}
```

---

#### 15. `GET /reports/:id`
Lấy chi tiết toàn văn báo cáo phân tích, bao gồm nhận định điều hành từ Claude API và link tải file JSON gốc từ S3.
- **Role:** All
- **Response 200:**
```json
{
  "report_id": "rep_20261021",
  "period": "2026-10-21",
  "status": "ready",
  "ai_status": "ai_generated",
  "summary": "### Báo cáo Thị trường Hằng ngày (2026-10-21)\n\n**Tóm tắt điều hành:**\nTrong 24 giờ qua, hệ thống đã theo dõi 42 sản phẩm...",
  "metrics": {
    "total_products": 42,
    "total_observations": 128,
    "anomaly_count": 2
  },
  "anomalies": [
    {
      "product_id": "iphone-15",
      "source_id": "tiki",
      "change_percent": -18.5
    }
  ],
  "download_url": "https://reports-bucket.s3.amazonaws.com/tenants/tenant-a/reports/rep_20261021.json?AWSAccessKeyId=...",
  "created_at": "2026-10-21T01:00:05Z"
}
```

---

#### 16. `GET /members`
Danh sách các thành viên trong tenant hiện tại.
- **Role:** Admin
- **Response 200:**
```json
{
  "data": [
    {
      "user_id": "user-01",
      "email": "admin@example.com",
      "role": "admin",
      "status": "active"
    },
    {
      "user_id": "user-02",
      "email": "analyst@example.com",
      "role": "analyst",
      "status": "active"
    }
  ],
  "total": 2,
  "max_members": 5
}
```

---

#### `POST /tenants`
Người dùng đã xác nhận email nhưng chưa thuộc tenant nào tự tạo workspace và trở thành Admin. Server tự sinh `tenant_id`; mỗi tài khoản chỉ tạo được một lần.
- **Role:** Đã đăng nhập, chưa có membership (`GET /me` trả 403 `membership_unavailable`)
- **Request Body:** `{ "name": "Shop ABC" }` (2-80 ký tự)
- **Response 201:** `{ "tenant_id": "t-...", "name": "Shop ABC", "role": "admin", "status": "active" }`
- **Lỗi:** 400 `invalid_tenant_name`, 403 `verified_email_required`, 409 `user_already_has_membership`

#### 17. `POST /members/invite`
Cấp quyền cho một người đã tự tạo tài khoản, theo email (Chặn nếu tenant $\ge$ 5 thành viên). Không tạo tài khoản và không gửi email. Trả 404 `user_not_registered` nếu email chưa có tài khoản đã xác nhận, 409 nếu người đó đã thuộc tenant khác.
- **Role:** Admin
- **Request Body:**
```json
{
  "email": "new_analyst@example.com",
  "role": "analyst"
}
```
- **Response 201:**
```json
{
  "email": "new_analyst@example.com",
  "role": "analyst",
  "status": "active",
  "message": "Đã thêm thành viên thành công."
}
```
- **Error 429 (Nếu vượt quá 5 thành viên):**
```json
{
  "error": {
    "code": "QUOTA_EXCEEDED",
    "message": "Đã đạt giới hạn tối đa 5 thành viên của gói Pilot."
  }
}
```

---

#### 18. `PATCH /members/:id`
Cập nhật vai trò (Admin đổi role) hoặc thay đổi trạng thái kích hoạt của thành viên.
- **Role:** Admin
- **Path Params:** `id` (hoặc `user_id`, ví dụ `user-02`)
- **Request Body:**
```json
{
  "role": "analyst",
  "status": "active"
}
```
*Lưu ý: Có thể truyền một trong hai hoặc cả hai trường `role` ('admin', 'analyst', 'viewer') và `status` ('active', 'disabled').*
- **Response 200:**
```json
{
  "user_id": "user-02",
  "email": "analyst@example.com",
  "role": "analyst",
  "status": "active",
  "message": "Cập nhật vai trò thành viên thành công."
}
```
- **Error 400 (Nếu role hoặc status không hợp lệ):**
```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "role must be one of: admin, analyst, viewer"
  }
}
```
- **Error 404 (Nếu không tìm thấy thành viên):**
```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Member does not exist in this tenant"
  }
}
```

---

## 4. Cam kết Tiến độ Triển khai của Hiếu

Để Nhật Minh dễ dàng lên lịch phát triển giao diện:

- **Tuần 2 (Hoàn thành trước ngày 28/10):**
  - Đã có sẵn: `GET /me`, `GET /dashboard/stats`, `GET /alerts`, `GET /sources`.
  - Minh có thể dựng khung Wireframe, Header, Navigation và Dashboard cards.
- **Tuần 3 (Hoàn thành trước ngày 04/11):**
  - Mở các endpoint thao tác: `POST /sources`, `PATCH /sources/:id`, `DELETE /sources/:id`, `POST /jobs`, `GET /jobs/:id/status`, `GET /matrix`, `GET /products`, `PATCH /products/:id/match`.
  - Minh hoàn thiện luồng Upload CSV UI và màn hình Duyệt ghép SKU.
- **Tuần 4 (Hoàn thành trước ngày 11/11):**
  - Mở các endpoint phân tích & quản trị: `GET /products/:id/price-history`, `GET /reports`, `GET /reports/:id`, `GET /members`, `POST /members/invite`, `PATCH /members/:id`.
  - Minh hoàn thiện màn hình Báo cáo AI, biểu đồ giá 30 ngày và trang Quản trị thành viên (gồm chức năng phân quyền/đổi role).
