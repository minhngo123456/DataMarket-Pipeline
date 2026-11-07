# Kịch bản demo ngày 14/11/2026

Demo khoảng 10 phút trên môi trường dùng chung `marketdata-dev` (Region `ap-southeast-1`). Cấu hình và kết quả đã đo nằm trong [trạng thái triển khai](deployment-status.md); chỉ trình bày những gì có trong tài liệu đó hoặc thấy trực tiếp lúc demo.

| Mục | Giá trị |
| --- | --- |
| Trang web | https://main.d1v1jrtzpab44p.amplifyapp.com |
| Workspace demo | `group07` (3 nguồn: `vendor-a`, `vendor-b` dạng CSV, `vendor-c` dạng REST API) |
| Admin | `ngominhexstra@gmail.com` |
| Viewer | `hihihahahohohehe15@gmail.com` (cần biết mật khẩu trước buổi demo) |
| Tài khoản đăng ký thử | Một email khác, nhận được thư ngay tại chỗ |

## Chuẩn bị

Giá chỉ được tính nếu quan sát trong 24 giờ trước thời điểm báo cáo. Báo cáo 08:00 ngày 14/11 gọi Gemini chỉ khi có giá quan sát sau 08:00 ngày 13/11; nếu không, báo cáo ghi `skipped_no_data`. Vì vậy làm các bước dưới vào **tối 13/11, trước 22:00**.

1. Đăng nhập AWS: `aws login --profile marketdata-dev`.
2. Tạo dữ liệu mới cho cả 3 nguồn, từ thư mục gốc của repo:

   ```powershell
   C:\dmv\Scripts\python.exe -m scripts.make_demo_csvs --out-dir $env:USERPROFILE\Desktop\demo-csv --vendor-c-feed apps/web/public/demo/vendor-c-api.json
   ```

3. Deploy lại web để `vendor-c` đọc được file mới (lần chạy theo lịch tiếp theo, mỗi 6 giờ vào phút 15 lúc 04:15, 10:15, 16:15, 22:15 giờ Việt Nam):

   ```powershell
   C:\dmv\Scripts\python.exe -m scripts.build_web_env --stack marketdata-dev --region ap-southeast-1 --profile marketdata-dev
   pnpm --dir apps/web build
   C:\dmv\Scripts\python.exe -m scripts.deploy_web_amplify --app-id d1v1jrtzpab44p --region ap-southeast-1 --profile marketdata-dev
   ```

   Không commit file `vendor-c-api.json` đã đổi nếu không cần; thời gian trong file chỉ có tác dụng 24 giờ.
4. Đăng nhập Admin, vào **Tải dữ liệu**, tải `demo-vendor-a.csv` cho nguồn `vendor-a` và `demo-vendor-b.csv` cho `vendor-b`. Mỗi tệp phải báo 5 accepted, 0 rejected. Giữ thêm một bản CSV mới để tải lại trong buổi demo.
5. Sáng 14/11 sau 08:00: mở **Báo cáo**, kiểm tra báo cáo ngày 14/11 có `ai_generated` và email đã đến hộp thư Admin. Đọc trước phần khuyến nghị.
6. Chụp màn hình dự phòng: ma trận giá, báo cáo, email, trang Nguồn, danh sách alarm trong CloudWatch.
7. Thử đăng nhập Viewer và kiểm tra email đăng ký thử nhận được mã.

## Kịch bản

| # | Thời gian | Thao tác | Kết quả cần thấy |
| --- | --- | --- | --- |
| 1 | 1 phút | Giới thiệu bài toán và sơ đồ kiến trúc: web Amplify, Cognito, HTTP API, Lambda, SQS/DLQ, DynamoDB, S3, EventBridge Scheduler, SNS, Gemini qua Secrets Manager. | Người nghe nắm luồng nguồn → chuẩn hóa → ma trận → báo cáo. |
| 2 | 0,5 phút | Đăng nhập bằng Admin `group07`. | Vào Tổng quan của `group07`. |
| 3 | 1 phút | Mở **Nguồn dữ liệu**. | 3 nguồn, mỗi nguồn có trạng thái thu thập và lần chạy gần nhất; `vendor-c` chạy tự động 6 giờ một lần. |
| 4 | 1,5 phút | Mở **Tải dữ liệu**, tải CSV mới cho `vendor-a`. | Tệp đi thẳng lên S3; job hiện 5 accepted, 0 rejected sau vài giây. |
| 5 | 1 phút | Mở **Tổng quan**, xem ma trận giá 24 giờ, rồi lịch sử giá một sản phẩm. | Giá theo từng nguồn; nguồn quá hạn được đánh dấu. |
| 6 | 1,5 phút | Mở **Báo cáo** ngày 14/11 và email trong hộp thư Admin. | Báo cáo Gemini nêu đúng nguồn và giá; email cùng nội dung. Nói rõ AI chỉ hỗ trợ, không tự đổi giá. |
| 7 | 1,5 phút | Trong cửa sổ ẩn danh: **Tạo tài khoản** bằng email thử, nhập mã, tạo workspace mới. | Workspace mới trống, không thấy nguồn, sản phẩm hay báo cáo của `group07`. |
| 8 | 1 phút | Đăng nhập Viewer `group07`. | Thấy dữ liệu nhưng không có mục Tải dữ liệu, Danh mục sản phẩm, Thành viên. |
| 9 | 1 phút | Mở CloudWatch: 7 alarm và 6 DLQ. | Tất cả `OK`; nói kết quả kiểm thử lỗi đã đo (DLQ, retry, chặn URL nội bộ, lỗi gửi thông báo). |
| 10 | 0,5 phút | Tóm tắt kết quả kiểm thử và giới hạn. | Dùng bảng trong [trạng thái triển khai](deployment-status.md). |

Không chạy `e2e_failures` trong buổi demo: mất khoảng 20 phút và cố ý đưa alarm sang `ALARM`.

## Nếu có sự cố

| Sự cố | Cách xử lý |
| --- | --- |
| Báo cáo 14/11 là `skipped_no_data` hoặc `fallback_quantitative` | Trình bày ảnh chụp báo cáo Gemini ngày 05/11 và giải thích cơ chế; báo cáo kích hoạt tay chỉ được 1 lần mỗi ngày nên không tạo lại được. |
| Không nhận được mã đăng ký | Dùng ảnh chụp và kết quả đã đo trong tài liệu; xem mục Spam. |
| Job CSV không xong sau 1 phút | Mở lại trang job; nếu vẫn chưa xong, dùng ảnh chụp ma trận. |
| Mạng chậm | Dùng ảnh chụp dự phòng theo đúng thứ tự kịch bản. |

## Sau demo

- Xóa workspace và tài khoản đăng ký thử (Cognito user, các bản ghi `T#<tenant>` và `U#<sub>`), giữ `group07`.
- Kiểm tra lại không còn alarm hoặc DLQ khác 0.
