# Ingestion workers

- `csv_worker.py`: adapter CSV tham chiếu do Hoàng làm để kiểm tra luồng S3/EventBridge/SQS/Normalizer. Hướng có thể nối ánh xạ cột vào contract đã chốt.
- `dispatcher.py`: Scheduler gọi Job Dispatcher/Report Dispatcher. Job ID dựa vào tenant, source và kỳ lịch, không dựa vào thời điểm retry.

API/HTML extractor và xử lý trạng thái nguồn đầy đủ thuộc phần Hướng, chưa được viết ở đây. [Contract bàn giao](../../docs/hoang-handover.md) mô tả trường bắt buộc, giới hạn và fixture.
