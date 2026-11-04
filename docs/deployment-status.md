# Trạng thái triển khai của nhóm

Cập nhật ngày 04/11/2026, cho stack `marketdata-dev` tại Region `ap-southeast-1`. Code đang chạy gồm bản hợp nhất, báo cáo Gemini và tự đăng ký tài khoản. Stack `marketdata-hoang-dev` tại `us-east-1` là môi trường riêng của Hoàng; kết quả của stack đó nằm trong [bàn giao của Hoàng](hoang-final-handover.md).

## Cấu hình đang chạy

| Mục | Giá trị |
| --- | --- |
| Giao diện | Amplify app `marketdata-dev-web`, `https://main.d1v1jrtzpab44p.amplifyapp.com`, tạo bằng CLI, ngoài template |
| `WebOrigin`, `LocalDevOrigin` | URL Amplify ở trên, `http://localhost:5173` |
| `EnableCloudFront` | `DISABLED`; lần bật trên tài khoản này rollback vì tài khoản chưa được xác minh |
| `ScheduleState` | `ENABLED`: lấy dữ liệu mỗi 6 giờ, báo cáo 08:00 giờ Việt Nam |
| `GeminiSecretArn` | Secret `marketdata-dev/gemini`, JSON có trường `api_key`; báo cáo dùng model `gemini-3.5-flash-lite` (`GeminiModel`) |
| `AnthropicSecretArn` | Trống; không gọi Claude |
| Đăng ký | Tự đăng ký bằng email và mã xác nhận (`AllowAdminCreateUserOnly=false`); mỗi tài khoản thuộc 1 workspace |
| `AlarmEmail` | Trống; 7 alarm gắn `AlarmTopic` nhưng topic chưa có người nhận, nên chỉ xem được trong CloudWatch |
| Tenant | `group07`, 2 member, 3 source |
| Email báo cáo | Stack `marketdata-dev-tenant-group07`, 1 subscription đã xác nhận |

## Kết quả kiểm thử

Chạy ngày 04/11/2026 sau khi deploy bản hợp nhất, trước khi thêm Gemini và tự đăng ký, trừ các dòng có ghi khác. Dữ liệu thử đã được xóa và kiểm tra lại bằng dry-run.

| Kiểm thử | Lệnh | Kết quả |
| --- | --- | --- |
| Python, bản hiện tại | `pytest -q` | 366 passed |
| Web, bản hiện tại | `pnpm test` | 42 passed |
| CSV đầu cuối | `scripts.smoke_test_aws --new` | 100 dòng: 90 accepted, 0 pending, 10 rejected; tenant thứ hai có 0 quan sát; phát lại không đổi kết quả |
| Quyền và cách ly tenant | `scripts.e2e_permissions --yes` | 21 PASS, 0 FAIL, 1 SKIP |
| Tình huống lỗi | `scripts.e2e_failures --yes` | 17 PASS, 0 FAIL |
| Email | `aws sns publish` tới topic của `group07` | Người nhận xác nhận email đã đến |
| Gemini, bản hiện tại | 1 báo cáo qua API trên tenant tạm, CSV demo 2 nguồn | `ai_status=ai_generated`, `ai_provider=gemini`; phần văn bản đã đối chiếu (1000 ký tự đầu) khớp dữ liệu, phần khuyến nghị chưa đối chiếu |
| Tự đăng ký, bản hiện tại | Thao tác tay ở web chạy cục bộ | Tài khoản `CONFIRMED`, workspace mới có đúng 1 Admin và không có dữ liệu của `group07` |

SKIP ở kiểm thử quyền là phần che header, vì các source của `group07` không có header. Phần này được kiểm tra thật trong `e2e_failures`, trên tenant tạm.

`e2e_failures` kiểm tra các tình huống sau:

| Tình huống | Kết quả quan sát |
| --- | --- |
| URL `169.254.169.254` | Job `failed` với `source_destination_blocked`, không retry, không vào DLQ |
| URL trả 404 | Job `failed` với `api_http_error_404`, không retry, không vào DLQ |
| URL không kết nối được | Hết giới hạn retry thì job `failed`, message vào `APIQueueDLQ`, `APIQueueDLQAlarm` chuyển `ALARM` |
| Publish SNS thất bại | `NOTIF#` ghi `failed` và `sns_publish_failed`, `NotificationFunctionErrorsAlarm` chuyển `ALARM` |
| Báo cáo khi không có key (trước khi thêm Gemini) | `status=ready`, `ai_status=mock_generated`, có file trong S3 |
| Header của source | Admin đọc được giá trị thật, Viewer thấy `***`; gửi lại `***` không đổi giá trị đã lưu |

## Giới hạn đã biết

- **Đăng ký mở cho mọi người có link.** Chưa có giới hạn tổng số tài khoản hoặc workspace. Mỗi workspace có báo cáo 08:00; khi có dữ liệu thì gọi Gemini, nên quota vẫn có thể bị tiêu hao. Báo cáo không có giá hợp lệ không gọi AI (`ai_status=skipped_no_data`).
- **Key Gemini gói miễn phí.** Nội dung gửi đi có thể được Google dùng để cải thiện sản phẩm theo điều khoản gói không trả phí; không gửi dữ liệu giá thật của doanh nghiệp qua gói này.
- **Báo cáo Gemini theo lịch của `group07` chưa được kiểm chứng.** Đã thử trên tenant tạm; lần theo lịch đầu tiên là 08:00 ngày 05/11. Báo cáo 08:00 ngày 04/11 chạy trên code cũ: `NOTIF#` ghi `sent`, nhưng báo cáo có 0 sản phẩm và cả 3 nguồn bị coi là quá hạn.
- **Lỗi Gemini thật chỉ có unit test.** `fallback_quantitative` đã xảy ra trên AWS một lần do secret rỗng; lỗi từ phía API Gemini chưa được thử.
- **Các kiểm thử quyền và lỗi chưa chạy lại trên bản hiện tại.** `e2e_permissions` và `e2e_failures` chạy trước khi thêm Gemini và tự đăng ký.
- **Dữ liệu demo nhanh quá hạn.** Giá cũ hơn 24 giờ bị loại khỏi ma trận. File `vendor-c` ghi cố định `observed_at` là 02/11, nên chạy lại theo lịch không tạo giá mới. Trước demo cần tải CSV mới và cập nhật file này.
- **`NotificationFunction` không có DLQ.** Sau khi hết retry bất đồng bộ, sự kiện bị bỏ; chỉ còn bản ghi `NOTIF#` và alarm.
- **API lưu cả URL địa chỉ nội bộ.** `POST /sources` nhận `169.254.169.254`; việc chặn diễn ra ở worker lúc lấy dữ liệu.
- **Snapshot nguồn và trạng thái thu thập chưa được kiểm tra riêng trên AWS.** API và giao diện cũng chưa hiển thị các trường trạng thái này, theo [hợp đồng trạng thái thu thập](source-collection-status.md).
- **Chưa đo tải, SLA hoặc chi phí vận hành thực tế.** KMS key và Slack secret của tenant có phí theo tháng và được giữ lại khi xóa stack.

## Thêm kênh email cho tenant

Chạy từ thư mục gốc của repo, sau khi có output `NotificationKeyArn` của stack chính:

```powershell
aws cloudformation deploy --region ap-southeast-1 --profile marketdata-dev --stack-name marketdata-dev-tenant-<tenant> --template-file infra/aws/tenant-notifications.yaml --parameter-overrides MainStackName=marketdata-dev TenantId=<tenant> NotificationKeyArn=<NotificationKeyArn> Email=<email>
```

Người nhận bấm link xác nhận trong email từ AWS. Sau đó ghi topic vào META bằng `update-item`, không dùng `put-item` vì sẽ đè các trường khác:

```powershell
aws dynamodb update-item --region ap-southeast-1 --profile marketdata-dev --table-name <ControlTable> --key '{\"pk\":{\"S\":\"T#<tenant>\"},\"sk\":{\"S\":\"META\"}}' --update-expression "SET sns_topic_arn = :t" --condition-expression "attribute_exists(pk)" --expression-attribute-values '{\":t\":{\"S\":\"arn:aws:sns:ap-southeast-1:<account>:marketdata-dev-tenant-<tenant>\"}}'
```

Tên topic phải đúng `<stack>-tenant-<tenant>`, nếu không `NotificationFunction` sẽ từ chối. Tenant chưa có `sns_topic_arn` thì báo cáo vẫn được tạo nhưng không gửi email, và không tự gửi lại sau khi thêm kênh.

## Thêm thành viên vào workspace

Người dùng tự tạo tài khoản ở trang đăng nhập trước, rồi Admin nhập đúng email đó ở trang Thành viên. Hệ thống không tạo tài khoản và không gửi email mời. Tài khoản chưa xác nhận hoặc đã thuộc workspace khác sẽ bị từ chối.

## Chạy lại kiểm thử

Chạy `scripts.stage_lambda` trước `sam build`, vì template đóng gói từ `build/lambda`.

```powershell
python -m scripts.smoke_test_aws --stack marketdata-dev --region ap-southeast-1 --profile marketdata-dev --new
python -m scripts.cleanup_smoke_tenants --stack marketdata-dev --region ap-southeast-1 --profile marketdata-dev --yes
python -m scripts.e2e_permissions --stack marketdata-dev --region ap-southeast-1 --profile marketdata-dev --yes
python -m scripts.e2e_permissions --stack marketdata-dev --region ap-southeast-1 --profile marketdata-dev --cleanup --yes
python -m scripts.e2e_failures --stack marketdata-dev --region ap-southeast-1 --profile marketdata-dev --yes
python -m scripts.e2e_failures --stack marketdata-dev --region ap-southeast-1 --profile marketdata-dev --cleanup --yes
```

Khi bỏ `--yes`, các lệnh `e2e_*` và lệnh dọn chỉ in kế hoạch. `e2e_failures` mất khoảng 20 phút và cố ý đưa 2 alarm sang `ALARM`; alarm tự về `OK` sau đó. `e2e_permissions` thêm tạm một Viewer vào `group07` cho đến khi dọn.
