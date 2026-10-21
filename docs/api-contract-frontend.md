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

## 2. Danh mục 17 Endpoints Chi tiết

| # | Method | Path | Mô tả | Role | Tuần cần |
|---|---|---|---|---|:---:|
| 1 | `GET` | `/me` | Thông tin người dùng + tenant + vai trò | All | 2 |
| 2 | `GET` | `/dashboard/stats` | Tổng SKU, số nguồn, trạng thái lỗi, report mới nhất | All | 2 |
| 3 | `GET` | `/alerts` | Danh sách cảnh báo giá/nguồn bất thường | All | 2 |
| 4 | `GET` | `/sources` | Danh sách nguồn dữ liệu + trạng thái hoạt động | All | 2 |
| 5 | `POST` | `/sources` | Thêm cấu hình nguồn mới (Tối đa 3 nguồn) | Admin, Analyst | 3 |
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
      "enabled": true,
      "updated_at": "2026-10-20T12:00:00Z"
    }
  ],
  "total": 2,
  "page": 1,
  "limit": 20
}
```

---

### Nhóm 2: Nguồn, Jobs & Upload CSV, Ma trận Giá (Tuần 3)

#### 5. `POST /sources`
Thêm nguồn dữ liệu mới (Hệ thống tự động chặn nếu tenant đã đạt tối đa 3 nguồn).
- **Role:** Admin, Analyst
- **Request Body:**
```json
{
  "source_id": "web-competitor-b",
  "name": "Website Đối thủ B",
  "kind": "web",
  "enabled": true
}
```
- **Response 201:**
```json
{
  "source_id": "web-competitor-b",
  "name": "Website Đối thủ B",
  "kind": "web",
  "enabled": true,
  "status": "configured"
}
```
- **Error 429 (Nếu đã có 3 nguồn):**
```json
{
  "error": {
    "code": "QUOTA_EXCEEDED",
    "message": "Đã đạt giới hạn tối đa 3 nguồn dữ liệu của gói Pilot."
  }
}
```

---

#### 6. `PATCH /sources/:id`
Bật/tắt hoặc sửa tên nguồn dữ liệu.
- **Role:** Admin, Analyst
- **Request Body:**
```json
{
  "name": "Website Đối thủ B (Cập nhật)",
  "enabled": false
}
```
- **Response 200:**
```json
{
  "source_id": "web-competitor-b",
  "enabled": false,
  "status": "updated"
}
```

---

#### 7. `DELETE /sources/:id`
Xóa cấu hình một nguồn dữ liệu.
- **Role:** Admin
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
  "filename": "bang_gia_ngay_21.csv"
}
```
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

#### 17. `POST /members/invite`
Thêm / phân quyền cho một thành viên mới vào tenant (Chặn nếu tenant $\ge$ 5 thành viên).
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

## 4. Cam kết Tiến độ Triển khai của Hiếu

Để Nhật Minh dễ dàng lên lịch phát triển giao diện:

- **Tuần 2 (Hoàn thành trước ngày 28/10):**
  - Đã có sẵn: `GET /me`, `GET /dashboard/stats`, `GET /alerts`, `GET /sources`.
  - Minh có thể dựng khung Wireframe, Header, Navigation và Dashboard cards.
- **Tuần 3 (Hoàn thành trước ngày 04/11):**
  - Mở các endpoint thao tác: `POST /sources`, `PATCH /sources/:id`, `DELETE /sources/:id`, `POST /jobs`, `GET /jobs/:id/status`, `GET /matrix`, `GET /products`, `PATCH /products/:id/match`.
  - Minh hoàn thiện luồng Upload CSV UI và màn hình Duyệt ghép SKU.
- **Tuần 4 (Hoàn thành trước ngày 11/11):**
  - Mở các endpoint phân tích & quản trị: `GET /products/:id/price-history`, `GET /reports`, `GET /reports/:id`, `GET /members`, `POST /members/invite`.
  - Minh hoàn thiện màn hình Báo cáo AI, biểu đồ giá 30 ngày và trang Quản trị thành viên.
