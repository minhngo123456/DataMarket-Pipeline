# Đề xuất duyệt thử nghiệm AI đọc website - CHƯA ĐƯỢC DUYỆT

Người nhận: Vũ Minh Hoàng. Không gửi tự động; không chứa key, token, credential hay URL có chữ ký.

- Dịch vụ/tính năng: Gemini API cho selector DOM của WebWorker và phần AWS liên quan trong môi trường hiện có.
- Lý do: các website công khai có cấu trúc HTML khác nhau; cần AI gợi ý cách đọc khi adapter thông thường không nhận diện được. Không dùng AI thay cho việc xác minh giá/SKU.
- Phương án miễn phí/local đã làm: parser HTML/JSON-LD/feed hiện có, adapter bốn website, FakeAI/Moto và validator DOM. Các cách này kiểm thử được luồng nhưng chưa chứng minh khả năng suy luận selector của model thật.
- Tài khoản/Region: người phụ trách chốt đúng tài khoản Gemini/Google và tài khoản AWS, Region của môi trường định thử. Chưa tra tài khoản/billing thật và chưa tạo tài nguyên.
- Model/đơn giá/dự toán: cần Hoàng chốt model còn khả dụng, kiểm tra bảng giá và điều kiện sử dụng chính thức trên đúng tài khoản ngay trước khi bật. Chi phí model = token đầu vào x đơn giá đầu vào + token đầu ra x đơn giá đầu ra; cộng request/storage/runtime/log của AWS. Với phiên một tenant có tối đa 20 request model, mỗi request tối đa 24.000 byte context cộng prompt cố định và 1.600 token đầu ra. Byte không phải token; chưa có đơn giá/model xác nhận nên chưa có dự toán USD đáng tin cậy và không đề nghị gọi thật ở trạng thái hiện tại.
- Mức chi tối đa hiện được phép: 0. Nếu cần ngoại lệ có phí, Hoàng phải ghi rõ mức USD tối đa và model/tài khoản được duyệt; bộ đếm kỹ thuật không thay thế trần chi phí tại nhà cung cấp. Không coi credit/Free Tier là bằng chứng đủ.
- Thời gian/phạm vi đề xuất: thử thủ công một tenant demo, không bật lịch, tối đa 24 giờ; tối đa 2 lần AI/job và 20 lần/tenant/phiên. Không gửi dữ liệu nguồn có đăng nhập hoặc header bí mật cho AI.
- Theo dõi: đề xuất Hướng phụ trách thực hiện đúng job đã duyệt, Hoàng kiểm tra usage/billing thực tế và quyền cấu hình. Hai người cần xác nhận trước khi áp dụng.
- Kế hoạch tắt: đặt WEB_AI_MODE=off, bỏ cấu hình key tại môi trường thử sau khi kết thúc, giữ nguyên lịch tắt và không tạo thêm dịch vụ lưu secret. Cache selector có thời hạn 24 giờ; không tự xóa tài nguyên AWS có sẵn.

Chỉ điền WEB_AI_USAGE_APPROVAL_REF và cấu hình live sau khi nội dung duyệt hoặc xác minh bằng 0 có đủ tài khoản, model, đơn giá, dự toán, trần chi và thời gian. Gửi đề xuất này không đồng nghĩa đã được duyệt.
