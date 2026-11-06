# Normalizer

Handler: `services.normalizer.app.handler`. Quy tắc nghiệp vụ ở `marketdata/service.py` và `normalize.py`; DynamoDB ở `repository.py`. Lỗi giá/thời gian là receipt bị từ chối; lỗi envelope hoặc hạ tầng trả partial batch failure cho SQS thử lại.

SKU chưa xác nhận chờ Analyst/Admin duyệt. Ghi receipt và observation cùng transaction để chống trùng qua retry. Xem [contract và cách chạy](../../docs/hoang-handover.md).

Khi nguồn đã được tạo lại với generation khác, Normalizer bỏ các dòng của job cũ và chuyển job còn hoạt động sang `failed` với lỗi `source_generation_changed`. Bộ đếm phản ánh receipt đã lưu; kết quả thu thập thành công trước đó và cấu hình nguồn mới được giữ nguyên. Nếu cập nhật job lỗi, dòng đó được trả về trong partial batch failure để thử lại.
