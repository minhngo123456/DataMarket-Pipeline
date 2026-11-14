# AI hỗ trợ nhận diện website

## Đã bổ sung gì

WebWorker ưu tiên bộ đọc HTML/JSON-LD/feed và các adapter Phục Hy, Thiên Long, Fahasa, Kim Đồng đã có. Khi không nhận diện được sản phẩm hoặc trang chi tiết không có dữ liệu chuẩn, bước dự phòng có thể nhờ Gemini gợi ý các selector DOM. Không dùng prompt báo cáo giá cho tác vụ đọc web.

AI chỉ trả quy tắc chọn thẻ/thuộc tính và tiền tố văn bản. Code tự lấy SKU, tên, giá, tiền tệ, biến thể và link từ DOM, kiểm tra dữ liệu rồi mới tạo snapshot. Không nhận giá/SKU do AI viết ra, không chạy code/regex do AI sinh, không cho AI gọi công cụ hoặc tự tải URL. Dòng được đọc bằng quy tắc này có nhãn AI hỗ trợ trong Catalog và vẫn đi qua chuẩn hóa, ghép SKU, quyền, quota và duyệt nhiều dòng hiện có.

Có hai kiểu quy tắc:

- Trang có đủ SKU và giá: xác định thẻ bao từng sản phẩm và selector cho từng trường.
- Trang chỉ có liên kết sản phẩm: xác định tối đa 10 link cùng origin, mở chi tiết và học thêm quy tắc cho mẫu trang chi tiết. Chỉ một cấp liên kết; tối đa hai lần gọi model trong một job.

Không có SKU hoặc bằng chứng giá/tiền tệ thì không tự bịa dữ liệu. Tiền tệ có thể đọc từ thẻ riêng hoặc dấu hiệu rõ VND/VNĐ/₫, GBP/£, EUR/€; riêng dấu `$` không đủ để đoán USD. Giá gạch ngang, nhiều field khớp, xung đột giá cho một SKU hoặc link khác origin bị từ chối. Chưa có trình duyệt headless: AI chỉ hỗ trợ nội dung HTTP đã tải được, không tự hiện dữ liệu đang chờ JavaScript, đăng nhập hoặc vượt chặn bot.

## Mặc định và điều kiện bật thật

**Mặc định `WEB_AI_MODE=off`, không phát sinh lời gọi AI.** Không tự dùng key/secret hay quota AI đang dành cho báo cáo. Không bổ sung Secrets Manager, quyền đọc secret, tài nguyên, lịch hoặc deployment AWS.

Để chạy thật, người phụ trách môi trường phải đáp ứng AGENTS.md: chứng minh toàn bộ thao tác không phát sinh phí trên môi trường thực tế, hoặc có ngoại lệ được Vũ Minh Hoàng duyệt. Các biến sau chỉ cấu hình phía server sau bước đó:

| Biến | Ý nghĩa |
| --- | --- |
| WEB_AI_MODE | `gemini` để cho phép bước dự phòng; `off` để tắt |
| WEB_AI_USAGE_APPROVAL_REF | Tham chiếu Hoàng duyệt hoặc hồ sơ xác minh chi phí bằng 0; không chứa secret |
| WEB_AI_APPROVED_UNTIL | Thời điểm hết hạn ISO UTC, phải còn hạn và không xa hơn 24 giờ tại lúc gọi |
| WEB_AI_TENANT_ALLOWLIST | Tenant được duyệt, phân tách bằng dấu phẩy |
| WEB_AI_MODEL | Model ID cụ thể đã được kiểm tra khả dụng và đơn giá; không tự chọn model thay thế |
| WEB_AI_API_KEY | Credential riêng được cấu hình bí mật ở server, không đưa vào VITE_*, Git, log hoặc tài liệu |

Không cần tạo dịch vụ lưu secret mới cho tính năng này. Nếu muốn dùng dịch vụ lưu secret thì phải duyệt chi phí và IAM riêng; code hiện tại không tự đọc secret AI báo cáo.

## Giới hạn và cache

- Tối đa 2 lượt AI/job, gồm các lần SQS redelivery và cả những lượt đã dành chỗ nhưng bị lỗi; không hoàn lại lượt vì khi crash không thể chắc nhà cung cấp chưa tính phí.
- Tối đa 20 lượt/tenant/phiên tham chiếu được duyệt, phiên tối đa 24 giờ. Bộ đếm lưu trong bảng Control hiện có, cập nhật có điều kiện để không bị reset khi Lambda khởi động lại. Trong mock local, dùng cửa sổ ngày UTC.
- Bộ đếm job và phiên được giữ chỗ tuần tự trước khi gọi model. Nếu bước giữ chỗ sau thất bại thì lượt trước vẫn bị trừ; cách này có thể dùng ít hơn hạn mức nhưng không gọi vượt hạn mức.
- Gửi tối đa 24.000 byte DOM đã lọc, cộng prompt cố định; tối đa 1.600 output token và 5 giây/request model. Không retry HTTP của model tự động và không tự tăng model, timeout hoặc quota.
- Không gửi HTTP headers, token, cookie, tenant/job ID; bỏ script, form, input, comment, URL query/fragment và nội dung email khỏi context. Nguồn có header ngoài User-Agent/Accept/Accept-Language/Accept-Encoding hoặc URL có userinfo không được dùng live AI fallback.
- DOM có tối đa 2.000 node, sâu 50 tầng. Trường giá phải khớp duy nhất trong card, giá dương và hợp đồng số/tiền tệ vẫn được kiểm tra.
- Quy tắc chỉ được lưu khi toàn bộ lượt đọc hợp lệ. Cache ở nguồn của đúng tenant/source generation, ràng buộc hash URL, hết hạn sau 24 giờ. Mỗi lần dùng cache phải chạy lại quy tắc trên HTML mới; giá không lấy từ cache.
- Đổi URL hoặc xóa/tạo lại nguồn không được kế thừa quy tắc cũ. Đổi cấu hình trong lúc học không được ghi quy tắc vào cấu hình mới. Cache nội bộ không trả qua GET /sources và client không được ghi nó.
- Có cache hợp lệ thì job sau không gọi model; snapshot replay không tải lại trang và không gọi lại AI. Lỗi một trang chi tiết không gửi một phần giá vào TransformQueue.

Đây là giới hạn kỹ thuật số lượt/token, không phải bằng chứng chi phí AWS/API bằng 0 hoặc giới hạn hóa đơn USD ở tài khoản nhà cung cấp. Hạn mức dự án vẫn là 0 cho đến khi có xác minh hoặc phê duyệt; phải tính cả DynamoDB/S3/Lambda/SQS/log đi kèm.

## Kiểm thử và phát hành

`tests/test_web_ai.py` dùng FakeAI và Moto, chặn network trong luồng tích hợp. Test transport dùng opener giả và key fixture không có quyền. Không có kết quả gọi model thật trong lần phát triển này và chưa đánh giá độ chính xác selector của một model triển khai thực tế.

Các trường hợp kiểm tra: trang có thẻ tùy biến, trang chỉ có link rồi mở chi tiết, cache đọc giá mới, replay snapshot, dữ liệu AI bịa/code, giá gạch ngang hoặc mơ hồ, thiếu SKU/currency, URL ngoài phạm vi, lọc context, giới hạn gọi bền vững, hết hạn phê duyệt, allowlist tenant và thay đổi source revision. Bốn website đã có adapter tiếp tục dùng đường đọc thông thường.

Muốn triển khai cần cập nhật gói WebWorker gồm `ai_rules.py`, `ai_client.py`, `ai_state.py` và các runtime chung. Phần tên hàng/duyệt nhiều dòng cần cả Normalizer, ApiFunction và frontend như hướng dẫn Catalog. Tính năng AI vẫn tắt khi chỉ phát hành code mà không cấu hình opt-in ở server. Không có tài nguyên/IAM mới trong template và không tự bật lịch.

Nút Thu thập ngay dùng được như cũ. Khi AI chưa được phép hoặc dữ liệu không kiểm chứng được, nguồn báo mã lỗi cụ thể, không hiển thị thành công giả. Các mã gồm `web_ai_approval_required`, `web_ai_not_configured`, `web_ai_budget_exhausted`, `web_ai_invalid_rules`, `web_ai_rules_no_match`, `web_ai_ambiguous_fields`, `web_ai_currency_missing`, `web_ai_private_source_blocked`, `web_ai_unavailable`.

Tham khảo khả năng trả JSON có cấu trúc của Gemini: https://aistudio.google.com/docs/structured-output. JSON hợp lệ không tự chứng minh giá/SKU là đúng; bộ xác minh DOM vẫn bắt buộc.
