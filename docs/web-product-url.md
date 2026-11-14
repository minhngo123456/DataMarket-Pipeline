# Thu thập giá bằng URL trang chủ Phục Hy

Worker đọc HTML tĩnh, không chạy JavaScript và không dùng dịch vụ mới. Ngoài bảng giá/data attributes cũ, worker hỗ trợ:

- **`https://phuchy.vn/`**: đọc tối đa 10 liên kết sản phẩm khác nhau đầu tiên trong thẻ `h3.pro-name`, theo thứ tự trên trang chủ. Worker mở các trang chi tiết để lấy đúng SKU, biến thể mặc định, giá và tiền tệ; mọi dòng thuộc cùng nguồn Phục Hy.
- Schema.org Product microdata: các thẻ `meta itemprop="sku"`, `price`, `priceCurrency` nằm trong phạm vi Product/Offer.
- JSON-LD Product với SKU (hoặc MPN) và Offer có `price`, `priceCurrency`; hỗ trợ mảng, `@graph` và ItemList nhúng Product.
- Ngoài trang chủ Phục Hy nói trên, worker chỉ đọc tài liệu tại URL cấu hình; không quét liên kết hay phân trang cho website khác. AggregateOffer chỉ có khoảng giá và nhiều giá khác nhau cho cùng SKU bị bỏ qua/báo lỗi, không chọn giá thấp nhất để thay cho một biến thể cụ thể.

Một lượt trang chủ có tối đa 11 request HTML (1 trang chủ và 10 trang chi tiết), mỗi response tối đa 1 MiB. Liên kết phát hiện phải là HTTPS, đúng host `phuchy.vn`, đường dẫn `/products/<slug>`; bỏ liên kết trùng, query và fragment. Worker kiểm tra ngân sách 20 giây trước mỗi request chi tiết và đặt timeout tối đa 3 giây cho request đó. Không thêm tài nguyên, quyền IAM hoặc thay đổi lịch chạy.

Nếu bất kỳ trang chi tiết nào lỗi, lượt đó chưa ghi snapshot và chưa gửi một phần sản phẩm vào hàng đợi. Sau khi đã lưu snapshot, retry dùng lại toàn bộ dữ liệu đã lưu, không đọc lại trang chủ hay trang chi tiết.

## Thao tác

1. Nguồn dữ liệu → Thêm nguồn → Trang web tĩnh → URL **`https://phuchy.vn/`**. Chỉ cần một nguồn web để lấy 10 sản phẩm; không tạo 10 nguồn riêng. URL chi tiết một sản phẩm vẫn được hỗ trợ.
2. Chọn **Giá đã gồm thuế** (`tax_included`) cho giá bán lẻ website Phục Hy theo chính sách công khai: https://phuchy.vn/pages/chinh-sach-thanh-toan. Với nguồn khác, chọn theo thông tin người bán. Nếu HTML có `price_basis` riêng thì giá trị từng dòng được ưu tiên.
3. Bấm lưu nguồn: giao diện gửi yêu cầu thu thập ngay và hiển thị **Chờ xử lý → Đang thu thập → Đã thu thập/Thu thập lỗi**. Nguồn đã tồn tại có nút **Thu thập ngay**; bật lại nguồn API/Web cũng tạo job. Trạng thái tự tải lại mỗi 3 giây trong tối đa 2 phút. Sau khi thu thập, vào Catalog duyệt SKU và ghép đúng sản phẩm/quy cách. CSV vẫn xử lý khi upload file.
4. Tổng quan → Cơ sở so sánh giá → chọn cùng cách tính thuế. Giá chưa xác định thuế chưa xuất hiện trong ma trận. Cần giá của các nguồn ghép cùng sản phẩm và cùng cách tính thuế để so sánh.

Biểu đồ lịch sử mở từ ma trận cũng chỉ hiển thị giá có cùng cách tính thuế đang chọn; giá thiếu thông tin thuế không được trộn vào biểu đồ. Các cách viết tương đương như `85000` và `85000.00` được coi là cùng một giá khi trang nhúng nhiều định dạng metadata.

Các SKU phải được ghép theo từng nguồn vào cùng sản phẩm catalog với CSV nội bộ. Tên/màu, quy cách và đơn vị bán phải tương ứng; ví dụ bút xanh `TL-027-XANH` không được ghép với bút màu khác. Danh sách trang chủ có thể thay đổi theo thời điểm nên CSV tham chiếu cần khớp danh sách thu thập ở lần demo đó. Fixture chỉ giữ mẫu markup tối thiểu với dữ liệu cố định.

Ưu tiên nguồn VND cho demo. Ngoại tệ được giữ nguyên mã tiền tệ, không đổi nhãn thành VND; Normalizer vẫn yêu cầu tỷ giá hợp lệ trong cấu hình server. Giao diện thay đổi này chưa thêm phần cấu hình tỷ giá.

## API

`POST /sources` và `PATCH /sources/{id}` nhận thêm `price_basis` cho nguồn `web`: `tax_included`, `tax_excluded`, `unknown`, hoặc `null` để bỏ mặc định. GET trả trường này nếu có. POST thay cấu hình nên thiếu trường sẽ xóa mặc định cũ; PATCH bỏ qua trường thì giữ nguyên.

`collect_now: true` yêu cầu tạo job và gửi vào APIQueue/WebQueue ngay sau khi lưu. Giao diện luôn gửi trường này khi lưu API/Web, bật lại nguồn hoặc bấm Thu thập ngay. Client API cũ bỏ trường hoặc gửi false vẫn chỉ lưu cấu hình; đây là cơ chế tương thích cho luồng theo lịch cũ. Trường phải là boolean; chỉ dùng cho nguồn API/Web đang bật và có URL. Response thành công có thêm `job_id` và `collection_status: queued`; nguồn queued chưa có thời điểm bắt đầu worker mới.

Thiếu biến môi trường hàng đợi trả 503 `source_collection_not_configured` trước khi lưu. Gửi hàng đợi lỗi trả 503 `source_collection_enqueue_failed`, `error.details.source_saved=true` và job_id; nguồn đã lưu nhưng job chưa được xác nhận gửi thành công. Không trả success giả. UI tải lại nguồn, cho phép thử lại bằng Thu thập ngay. Request/job không nhận tenant, generation, revision hoặc job ID từ client và không chứa URL/header nguồn trong message hàng đợi.

Giá trị mặc định được áp dụng lúc tạo snapshot. Retry giữ nguyên dữ liệu snapshot, kể cả khi cấu hình thuế của nguồn đã thay đổi. Trạng thái tenant, quyền, generation và giới hạn tải HTML 1 MiB/1.000 dòng vẫn áp dụng.

## Kiểm thử local

```powershell
.venv\Scripts\python.exe -m pytest tests/test_collect_on_save.py tests/test_phuc_hy_homepage.py tests/test_source_collection.py tests/test_source_pipeline.py tests/test_source_health_api.py tests/test_source_snapshots.py -q
cd apps/web
npm test -- src/pages/Sources src/pages/Dashboard
npm run build
```

Luồng kiểm thử dùng fixture/Moto và chặn network: cấu hình URL → Dispatcher → worker → snapshot → Normalizer → duyệt SKU → matrix, có kiểm tra tenant khác không thấy giá.

## Bàn giao triển khai

Nhánh: `huong`. Chưa triển khai thay đổi này lên AWS.

1. Lấy nhánh trên về và chạy kiểm thử local. Test trang chủ kiểm tra đủ 10 SKU, lỗi một trang không xuất dữ liệu thiếu, retry snapshot không tải lại và 10 sản phẩm đối sánh qua Moto với cách ly tenant. Ngày 14/11/2026: 94 test Python, 16 test giao diện nguồn, lint và build frontend qua; thử URL trang chủ thật ở local trả 10 SKU khớp CSV tham chiếu. Kết quả local không xác nhận luồng AWS thật.
2. Bản thu thập ngay cần phát hành **template SAM, ApiFunction, WebWorker và frontend**. Template bổ sung `API_QUEUE_URL`, `WEB_QUEUE_URL` cho ApiFunction và quyền `sqs:SendMessage` chỉ tới hai queue hiện có. Không tạo thêm queue, function, lịch hay migration dữ liệu. Chạy lại `python -m scripts.stage_lambda` ở local để đóng gói cả `services/api/source_collection.py`, `services/ingestion/product_html.py` và `services/ingestion/phuc_hy.py`; tránh dùng thư mục `build/lambda` cũ. Chỉ cập nhật frontend mà chưa cập nhật API/template sẽ báo lỗi cấu hình và không giả vờ đã gửi job.
3. Build và phát hành frontend với cấu hình công khai của đúng môi trường. Cần cấu hình Cognito thực tế khi build; thiếu cấu hình thì ứng dụng chỉ hiện hướng dẫn cấu hình. Không dùng giá trị fixture của kiểm thử để deploy và không đưa secret vào biến `VITE_*`.
4. Tạo nguồn web, chọn cách tính thuế và duyệt SKU. Cấu hình nguồn đã tồn tại vẫn dùng được; muốn thêm mặc định thuế cho nguồn cũ thì PATCH `price_basis`. Snapshot cũ giữ nguyên, cấu hình mới áp dụng khi thu thập snapshot cho job mới.

Ngân sách AWS mặc định là 0. Chỉ triển khai, kiểm thử AWS thật hoặc bật lịch sau khi đã xác minh chi phí bằng 0 trên đúng tài khoản/Region, hoặc có ngoại lệ được Vũ Minh Hoàng duyệt rõ ràng. Không tự bật lịch thu thập hoặc lịch báo cáo khi chỉ phát hành bản sửa này. Trong lúc chờ xác minh/duyệt, tiếp tục dùng fixtures và Moto.

Commit bàn giao dùng `[skip ci]` để không kích hoạt workflow GitHub Actions chưa xác minh chi phí. Các kết quả trên là kiểm thử local, không phải kết quả CI trên GitHub.
