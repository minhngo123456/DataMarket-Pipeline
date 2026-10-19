# Normalizer

Handler: `services.normalizer.app.handler`. Quy tắc nghiệp vụ ở `marketdata/service.py` và `normalize.py`; DynamoDB ở `repository.py`. Lỗi giá/thời gian là receipt bị từ chối; lỗi envelope hoặc hạ tầng trả partial batch failure cho SQS thử lại.

SKU chưa xác nhận chờ Analyst/Admin duyệt. Ghi receipt và observation cùng transaction để chống trùng qua retry. Xem [contract và cách chạy](../../docs/hoang-handover.md).
