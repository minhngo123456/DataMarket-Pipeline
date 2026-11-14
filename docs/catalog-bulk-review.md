# Tên hàng và duyệt nhiều SKU

## Cách dùng

1. Cập nhật đúng phiên bản, sau đó thu thập lại nguồn web để có metadata tên hàng. Dữ liệu cũ chỉ có mã không thể tự suy ra tên. API Catalog có thể dùng mô tả từ lần thu thập mới cho các dòng cũ cùng nguồn, cùng generation và SKU mà không sửa snapshot/giá gốc.
2. Vào Danh mục sản phẩm -> Chờ duyệt. Mỗi dòng có tên hàng, biến thể nếu nguồn có cung cấp, mã SKU và link Xem sản phẩm gốc. Tìm kiếm theo tên, biến thể hoặc SKU.
3. Tích checkbox hoặc bấm Chọn tất cả kết quả chờ duyệt để chọn toàn bộ kết quả đang lọc, kể cả ngoài trang bảng hiện tại. Dữ liệu này là các dòng đã tải từ API, không có thao tác ghi ẩn khi chọn.
4. Bấm Duyệt đã chọn. Cửa sổ xem trước gom các dòng quan sát cùng nguồn/SKU thành một mục. Mặc định mỗi mục tạo một sản phẩm riêng với tên hàng và biến thể; có thể sửa tên hoặc chọn sản phẩm catalog đã có nếu muốn ghép với nguồn khác. Mục thiếu tên phải nhập tên hoặc chọn sản phẩm có sẵn rồi mới xác nhận.
5. Bấm Xác nhận duyệt một lần. Giao diện gọi các API tạo sản phẩm/duyệt đã có, tuần tự và dùng nguyên kiểm tra tenant, quyền, quota, thời hạn receipt và source generation phía server. Kết quả thành công giữ nguyên nếu mục khác bị lỗi; báo số dòng thành công, lỗi cụ thể và giữ dòng chưa duyệt để sửa/thử lại. Đây không phải giao dịch nguyên khối cho cả danh sách.

Không tự ghép hai sản phẩm từ tên giống nhau, giá gần nhau hoặc SKU giống nhau ở hai nguồn. Mã sản phẩm mới trong duyệt nhiều được tạo từ nguồn/SKU với hậu tố để giảm va chạm; khi API báo xung đột ID không tự dùng sản phẩm trùng ID. Chọn rõ một sản phẩm có sẵn nếu muốn đối sánh chung. Nếu tạo sản phẩm thành công nhưng duyệt lỗi, sản phẩm đó vẫn tồn tại; lần thử lại trong cửa sổ dùng sản phẩm vừa tạo, không tạo lại.

Sau khi xác nhận mapping một nguồn/SKU, các lượt thu thập tiếp theo tự dùng mapping; không cần duyệt lại từng lần. Các receipt cũ còn pending có thể được chọn duyệt cùng lúc. Duyệt nhiều sản phẩm mới vẫn chịu quota catalog hiện hành (tối đa 100), không tự nâng gói.

## Metadata tùy chọn

- `source_name`: tên hàng gốc, tối đa 500 ký tự.
- `source_variant`: biến thể/quy cách do nguồn cung cấp, tối đa 200 ký tự. Không hiển thị nhãn Shopify `Default Title` như một biến thể thật.
- `source_url`: link HTTP(S) gốc, tối đa 2.048 ký tự; không nhận userinfo, ký tự điều khiển; bỏ query/fragment trước khi lưu/hiển thị để không mang tham số phiên theo link.

Haravan/Shopify lấy tên, title biến thể và handle từ feed. Product JSON-LD lấy name/url; trang đơn có thể dùng og:title hoặc title làm nhãn dự phòng. Fahasa lấy name từ Product JSON-LD, SKU vẫn lấy từ mã hàng của trang. Các trường chỉ là dữ liệu hiển thị, không tham gia tạo content_id, chọn giá, xác định tenant hoặc tự đổi mapping.

Các adapter CSV nhận thêm các cột trên nếu cần; API extractor giữ các trường đúng tên trên. Normalizer lọc/giới hạn metadata rồi giữ trong receipt và observation. GET /products trả metadata cho các dòng chờ duyệt/từ chối khi có. Metadata không có/sai định dạng được bỏ qua, không đoán tên từ mã số.

## Phát hành và kiểm thử

Thay đổi cần cập nhật gói runtime chứa `marketdata/product_metadata.py`, WebWorker/APIWorker, Normalizer, ApiFunction và frontend. Cập nhật riêng frontend không làm dữ liệu cũ có tên. Không thêm endpoint, tài nguyên AWS, IAM hay migration ghi lại giá cũ. Mọi kiểm thử tích hợp mặc định dùng fixture/Moto; không tự deploy hoặc dùng AWS thật nếu chưa đáp ứng quy tắc chi phí của dự án.

Các nhóm kiểm thử: `tests/test_product_metadata.py`, `tests/test_product_html.py`, `tests/test_bookstores.py`, `tests/test_storefront.py`, cùng CatalogPage và catalogBatch frontend. Bao phủ metadata đến Catalog, không lộ query/userinfo, metadata tách source generation/tenant, mapping tự áp dụng ở job sau, duyệt nhiều dòng, không gộp khác nguồn, quota/xung đột API và lỗi một phần.
