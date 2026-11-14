# Thu thập giá bằng URL sản phẩm

Worker đọc HTML tĩnh, không chạy JavaScript và không dùng dịch vụ mới. Ngoài bảng giá/data attributes cũ, worker hỗ trợ:

- Schema.org Product microdata: các thẻ `meta itemprop="sku"`, `price`, `priceCurrency` nằm trong phạm vi Product/Offer.
- JSON-LD Product với SKU (hoặc MPN) và Offer có `price`, `priceCurrency`; hỗ trợ mảng, `@graph` và ItemList nhúng Product.
- Chỉ đọc tài liệu tại URL cấu hình; không tự đi theo liên kết sản phẩm, phân trang hoặc tải thêm API. AggregateOffer chỉ có khoảng giá và nhiều giá khác nhau cho cùng SKU bị bỏ qua/báo lỗi, không chọn giá thấp nhất để thay cho một biến thể cụ thể.

## Thao tác

1. Nguồn dữ liệu → Thêm nguồn → Trang web tĩnh → dán URL **chi tiết sản phẩm**.
2. Chọn cách tính thuế theo thông tin người bán: đã gồm thuế, chưa gồm thuế hoặc chưa xác định. Không tự suy ra VAT từ tên website. Nếu HTML có `price_basis` riêng thì giá trị từng dòng được ưu tiên.
3. Sau lượt thu thập theo lịch hiện có (6 giờ), vào Catalog duyệt SKU và ghép với đúng sản phẩm/quy cách trong catalog.
4. Tổng quan → Cơ sở so sánh giá → chọn cùng cách tính thuế. Giá chưa xác định thuế chưa xuất hiện trong ma trận. Cần giá của các nguồn ghép cùng sản phẩm và cùng cách tính thuế để so sánh.

Biểu đồ lịch sử mở từ ma trận cũng chỉ hiển thị giá có cùng cách tính thuế đang chọn; giá thiếu thông tin thuế không được trộn vào biểu đồ. Các cách viết tương đương như `85000` và `85000.00` được coi là cùng một giá khi trang nhúng nhiều định dạng metadata.

Ví dụ URL đã kiểm tra cấu trúc HTML ngày 14/11/2026: https://phuchy.vn/products/giay-a4-double-a-70gsm — có SKU `DOUBLE-A-A4-70GSM`, giá và tiền tệ VND trong Product microdata. Việc đọc được metadata không xác nhận VAT, quyền thu thập định kỳ hoặc giá thanh toán cuối cùng. Fixture chỉ giữ mẫu markup tối thiểu với dữ liệu cố định.

Ưu tiên nguồn VND cho demo. Ngoại tệ được giữ nguyên mã tiền tệ, không đổi nhãn thành VND; Normalizer vẫn yêu cầu tỷ giá hợp lệ trong cấu hình server. Giao diện thay đổi này chưa thêm phần cấu hình tỷ giá.

## API

`POST /sources` và `PATCH /sources/{id}` nhận thêm `price_basis` cho nguồn `web`: `tax_included`, `tax_excluded`, `unknown`, hoặc `null` để bỏ mặc định. GET trả trường này nếu có. POST thay cấu hình nên thiếu trường sẽ xóa mặc định cũ; PATCH bỏ qua trường thì giữ nguyên.

Giá trị mặc định được áp dụng lúc tạo snapshot. Retry giữ nguyên dữ liệu snapshot, kể cả khi cấu hình thuế của nguồn đã thay đổi. Trạng thái tenant, quyền, generation và giới hạn tải HTML 1 MiB/1.000 dòng vẫn áp dụng.

## Kiểm thử local

```powershell
.venv\Scripts\python.exe -m pytest tests/test_product_html.py tests/test_api_web_workers.py tests/test_web_timestamps.py tests/test_source_pipeline.py tests/test_source_snapshots.py -q
cd apps/web
npm test -- src/pages/Sources src/pages/Dashboard
npm run build
```

Luồng kiểm thử dùng fixture/Moto và chặn network: cấu hình URL → Dispatcher → worker → snapshot → Normalizer → duyệt SKU → matrix, có kiểm tra tenant khác không thấy giá.

## Bàn giao triển khai

Nhánh: `huong`. Chưa triển khai thay đổi này lên AWS.

1. Lấy nhánh trên về và chạy kiểm thử local. Các kiểm tra đã thực hiện: hồi quy backend 186 test trước bản vá bổ sung, sau bản vá parser/lịch sử có 71 test Python và 11 test giao diện liên quan; lint và build frontend qua. Đã thử đọc HTML thật của URL Phục Hy nêu trên ở local, chưa xác nhận luồng AWS thật.
2. Phát hành backend trước: **ApiFunction và WebWorker**, gồm module mới `services/ingestion/product_html.py`. Chạy lại `python -m scripts.stage_lambda` ở local để cập nhật mã đóng gói; tránh dùng thư mục `build/lambda` cũ. Thay đổi này không yêu cầu thêm tài nguyên, quyền IAM, biến môi trường backend hoặc migration dữ liệu.
3. Build và phát hành frontend với cấu hình công khai của đúng môi trường. Cần cấu hình Cognito thực tế khi build; thiếu cấu hình thì ứng dụng chỉ hiện hướng dẫn cấu hình. Không dùng giá trị fixture của kiểm thử để deploy và không đưa secret vào biến `VITE_*`.
4. Tạo nguồn web, chọn cách tính thuế và duyệt SKU. Cấu hình nguồn đã tồn tại vẫn dùng được; muốn thêm mặc định thuế cho nguồn cũ thì PATCH `price_basis`. Snapshot cũ giữ nguyên, cấu hình mới áp dụng khi thu thập snapshot cho job mới.

Ngân sách AWS mặc định là 0. Chỉ triển khai, kiểm thử AWS thật hoặc bật lịch sau khi đã xác minh chi phí bằng 0 trên đúng tài khoản/Region, hoặc có ngoại lệ được Vũ Minh Hoàng duyệt rõ ràng. Không tự bật lịch thu thập hoặc lịch báo cáo khi chỉ phát hành bản sửa này. Trong lúc chờ xác minh/duyệt, tiếp tục dùng fixtures và Moto.

Commit bàn giao dùng `[skip ci]` để không kích hoạt workflow GitHub Actions chưa xác minh chi phí. Các kết quả trên là kiểm thử local, không phải kết quả CI trên GitHub.
