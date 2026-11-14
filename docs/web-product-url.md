# Thu thập giá bằng URL của nhiều website

Worker đọc HTML tĩnh, không chạy JavaScript và không dùng dịch vụ mới. Ngoài bảng giá/data attributes cũ, worker hỗ trợ:

- Đọc trực tiếp bảng giá/data attributes hoặc dữ liệu Product tại URL nhập vào. Khi chưa có giá, tự tìm tối đa 10 liên kết sản phẩm khác nhau cùng origin trên trang chủ hoặc danh mục; hỗ trợ đường dẫn `/products/<slug>`, `/product/<slug>`, `/san-pham/<slug>`, `/collections/<collection>/products/<slug>` và URL trong JSON-LD Product/ItemList. Ưu tiên heading sản phẩm nếu có. Không phân trang hoặc quét toàn bộ website.
- Schema.org Product microdata: SKU/giá/tiền tệ trong thẻ `meta`, `content` hoặc nội dung thẻ nằm trong phạm vi Product/Offer; hỗ trợ cả Open Graph có đủ loại Product, retailer SKU, giá và tiền tệ.
- JSON-LD Product với SKU (hoặc MPN) và Offer có `price`, `priceCurrency`; hỗ trợ mảng, `@graph` và ItemList nhúng Product.
- Nếu trang không có liên kết sản phẩm và nhận diện được Haravan/Shopify, đọc endpoint `products.json` công khai: ưu tiên danh mục ở `data-handle` của trang, hoặc chính danh mục đang nhập; Shopify còn hỗ trợ feed gốc. Lấy tối đa 10 sản phẩm, giữ từng SKU/giá của các biến thể còn bán (`available` khác false), không chọn giá thấp nhất. Trang chi tiết thiếu giá đọc JSON của chính sản phẩm đó, không thu thập sản phẩm gợi ý thay thế. Không thực thi JavaScript hoặc gọi URL tùy ý trong script.
- Tiền tệ feed phải có trong metadata analytics của shop; không lấy `Shopify.currency.active` vì có thể là tiền tệ hiển thị của khách thay vì tiền tệ gốc trong feed. Không có SKU/tiền tệ, feed sai hoặc nhiều giá xung đột cho cùng SKU sẽ báo lỗi. AggregateOffer chỉ có khoảng giá vẫn không được biến thành giá của một biến thể cụ thể.
- **Kim Đồng/Sapo và các mẫu thẻ tên sản phẩm**: nhận thêm link dạng `/ten-sach` hoặc `/ten-sach.html` khi nằm trong thẻ có class `item-product-name`, `product-item-link` hoặc heading `product-name`/`product-name-no-ellipsis`. Không lấy mọi link điều hướng có dạng slug. Mở tối đa 10 trang chi tiết để đọc SKU và giá Product/Offer.
- **`https://www.fahasa.com/`**: đọc cấu hình JSON của tab sản phẩm đầu tiên trong `current_block_data_str`, giới hạn 10 ID và gọi GET công khai `/tabslider/index/getdata/`. Chỉ dùng URL sản phẩm cùng origin trả về để mở trang chi tiết. SKU lấy từ khai báo `sku_product`, giá bán/tiền tệ lấy từ Offer JSON-LD có URL khớp chính sản phẩm. Không dùng `product_id` của feed làm SKU, không dùng giá bìa, không đoán tiền tệ và không đọc dữ liệu khách hàng/phiên đăng nhập. Thứ tự này là tab sản phẩm đầu tiên có cấu hình hỗ trợ, không phải mọi banner/flash sale trên trang chủ.

Một lượt có tối đa 11 request HTML (trang đầu + 10 trang chi tiết), 12 request với Fahasa (thêm một request danh sách JSON), hoặc 2 request khi đọc thẳng feed Haravan/Shopify (3 nếu thử trang chi tiết đầu tiên rồi chuyển sang feed). Mỗi response WebWorker nhận tối đa 10 MiB (10.485.760 byte), áp dụng cả HTML và JSON của storefront; đọc nhiều nhất giới hạn + 1 byte rồi dừng nếu quá mức, báo `html_response_exceeds_10_mib`. Giới hạn CSV/API riêng vẫn là 1 MiB. Toàn bộ snapshot tối đa 1.000 dòng. Liên kết phát hiện phải cùng scheme/host/port với URL nguồn, không có userinfo; bỏ liên kết trùng, query và fragment. Mọi request đi qua bộ kiểm tra IP công khai/chống SSRF hiện có. Worker kiểm tra ngân sách 20 giây trước và sau mỗi request bổ sung, timeout tối đa 3 giây/request. Không thêm tài nguyên, quyền IAM hoặc thay đổi lịch chạy.

Đã thử đọc công khai ở local ngày 14/11/2026: `https://phuchy.vn/` trả 10 SKU qua HTML; `https://thienlong.vn/` trả 42 SKU/biến thể thuộc 10 sản phẩm trong danh mục đầu tiên tải động. Với trang tải động, danh mục lấy từ trang chủ có thể khác thứ tự banner hoặc danh sách cá nhân hóa trên trình duyệt. Kết quả phụ thuộc dữ liệu website tại lúc chạy, không phải cam kết mọi trang đều hỗ trợ. Trang đăng nhập, CAPTCHA/chặn bot, thiếu SKU hoặc JavaScript tùy biến ngoài các định dạng trên trả lỗi rõ ràng; không vượt cơ chế chặn và không tự dùng dịch vụ có phí.

Sau khi bổ sung bộ đọc nhà sách, thử trực tiếp cùng ngày: trang chủ Fahasa trả 10 SKU trong khoảng 7,9 giây, Kim Đồng trả 10 SKU trong khoảng 18,1 giây. Đây là thời gian quan sát của từng lượt local, không phải SLA; nguồn chậm vẫn có thể timeout và đi qua cơ chế retry hiện có. Các test nhà sách dùng mẫu HTML/JSON tối thiểu, giá cố định giả lập, chặn DNS/TCP bằng Moto; không lưu toàn bộ trang thật hoặc thông tin phiên vào fixture.

Nếu bất kỳ trang chi tiết nào lỗi, lượt đó chưa ghi snapshot và chưa gửi một phần sản phẩm vào hàng đợi. Sau khi đã lưu snapshot, retry dùng lại toàn bộ dữ liệu đã lưu, không đọc lại trang chủ hay trang chi tiết.

## Thao tác

1. Nguồn dữ liệu → Thêm nguồn → Trang web → nhập URL trang chủ, danh mục hoặc sản phẩm (ví dụ **`https://phuchy.vn/`**, **`https://thienlong.vn/`**, **`https://www.fahasa.com/`**, **`https://nxbkimdong.com.vn/`**). Mỗi website cấu hình thành một nguồn. Giới hạn số nguồn theo gói hiện tại vẫn giữ nguyên; một nguồn có thể chứa nhiều SKU.
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

Giá trị mặc định được áp dụng lúc tạo snapshot. Retry giữ nguyên dữ liệu snapshot, kể cả khi cấu hình thuế của nguồn đã thay đổi. Trạng thái tenant, quyền, generation và giới hạn tải WebWorker 10 MiB/1.000 dòng vẫn áp dụng. Tăng giới hạn dung lượng không bổ sung khả năng phân tích một cấu trúc website chưa hỗ trợ; vẫn cần phát hành WebWorker mới để thay đổi có hiệu lực trên môi trường triển khai.

## Kiểm thử local

```powershell
.venv\Scripts\python.exe -m pytest tests/test_bookstores.py tests/test_web_response_limit.py tests/test_storefront.py tests/test_product_html.py tests/test_collect_on_save.py tests/test_phuc_hy_homepage.py tests/test_source_collection.py tests/test_source_pipeline.py tests/test_source_health_api.py tests/test_source_snapshots.py -q
cd apps/web
npm test -- src/pages/Sources src/pages/Dashboard
npm run build
```

Luồng kiểm thử dùng fixture/Moto và chặn network: cấu hình URL → Dispatcher → worker → snapshot → Normalizer → duyệt SKU → matrix, có kiểm tra tenant khác không thấy giá.

## Bàn giao triển khai

Nhánh: `huong`. Chưa triển khai thay đổi này lên AWS.

1. Chạy kiểm thử local ở trên. Test nhiều cấu trúc storefront, từng biến thể/tiền tệ, lỗi một trang không xuất dữ liệu thiếu, retry snapshot không tải lại và đối sánh qua Moto với cách ly tenant. Kết quả local không xác nhận luồng AWS thật.
2. Bản thu thập ngay cần phát hành **template SAM, ApiFunction, WebWorker và frontend**. Template bổ sung `API_QUEUE_URL`, `WEB_QUEUE_URL` cho ApiFunction và quyền `sqs:SendMessage` chỉ tới hai queue hiện có. Không tạo thêm queue, function, lịch hay migration dữ liệu. Chạy lại `python -m scripts.stage_lambda` ở local để đóng gói cả `services/api/source_collection.py`, `services/ingestion/product_html.py`, `services/ingestion/storefront.py` và `services/ingestion/fahasa.py`; tránh dùng thư mục `build/lambda` cũ. Bản mở rộng nhiều website chỉ sửa WebWorker và nội dung hướng dẫn frontend so với bản thu thập ngay. Nếu đã phát hành bản thu thập ngay, bổ sung nhà sách chỉ cần cập nhật gói WebWorker. Chỉ cập nhật frontend mà chưa cập nhật API/template sẽ báo lỗi cấu hình và không giả vờ đã gửi job.
3. Build và phát hành frontend với cấu hình công khai của đúng môi trường. Cần cấu hình Cognito thực tế khi build; thiếu cấu hình thì ứng dụng chỉ hiện hướng dẫn cấu hình. Không dùng giá trị fixture của kiểm thử để deploy và không đưa secret vào biến `VITE_*`.
4. Tạo nguồn web, chọn cách tính thuế và duyệt SKU. Cấu hình nguồn đã tồn tại vẫn dùng được; muốn thêm mặc định thuế cho nguồn cũ thì PATCH `price_basis`. Snapshot cũ giữ nguyên, cấu hình mới áp dụng khi thu thập snapshot cho job mới.

Ngân sách AWS mặc định là 0. Chỉ triển khai, kiểm thử AWS thật hoặc bật lịch sau khi đã xác minh chi phí bằng 0 trên đúng tài khoản/Region, hoặc có ngoại lệ được Vũ Minh Hoàng duyệt rõ ràng. Không tự bật lịch thu thập hoặc lịch báo cáo khi chỉ phát hành bản sửa này. Trong lúc chờ xác minh/duyệt, tiếp tục dùng fixtures và Moto.

Commit bàn giao dùng `[skip ci]` để không kích hoạt workflow GitHub Actions chưa xác minh chi phí. Các kết quả trên là kiểm thử local, không phải kết quả CI trên GitHub.
