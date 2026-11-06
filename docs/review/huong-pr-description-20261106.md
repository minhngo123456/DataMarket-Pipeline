# Hoàn thiện vòng đời nguồn và hiển thị trạng thái thu thập

## Mục tiêu

Khi nguồn bị xóa rồi tạo lại cùng ID, job cũ cần kết thúc mà không ghi dữ liệu vào nguồn thay thế. Thay đổi này gắn job/receipt với generation của nguồn, bảo vệ cập nhật cấu hình và bộ đếm khi xử lý đồng thời, kết thúc job obsolete ở `failed` và giữ ID observation hiện hành khi chạy migration. Ví dụ hồi quy: migration rồi nhập lại cùng ba dòng vẫn giữ ba observation, thay vì tăng thành sáu.

Trang Nguồn hiển thị kết quả thu thập, lần chạy/thành công gần nhất, mã lỗi an toàn và nút làm mới cho cả Viewer. Nhãn “Đã thu thập” được giải thích là nguồn đã được đọc và gửi dữ liệu để chuẩn hóa; chưa xác nhận các dòng đã được chấp nhận.

## Phạm vi ảnh hưởng

- Thành phần: repository, API nguồn/upload, Dispatcher, CSV/API/Web worker, Normalizer, script migration, trang Nguồn và test/tài liệu bàn giao.
- Backend: generation do server quản lý; job cũ không tác động vào nguồn tạo lại; thao tác cấu hình/DELETE có điều kiện và trả 409 khi xung đột; quota nguồn và snapshot bộ đếm được bảo vệ khi xử lý đồng thời. `enabled` phải là JSON boolean.
- Normalizer: cập nhật trạng thái lỗi và counts từ receipt bền vững trong một lần ghi có điều kiện; giữ nguyên job đã kết thúc và kết quả thu thập thành công. Lỗi ghi trạng thái obsolete được trả partial batch failure để retry.
- Migration: chỉ chuyển ID nhận diện được là công thức job/dòng cũ; ID hiện hành/không rõ generation được giữ nguyên, tránh gộp lịch sử của các lần tạo nguồn khác nhau. Không tự suy generation từ nguồn hiện tại.
- HTTP: chặn redirect HTTPS xuống HTTP trước khi chuyển tiếp header; redirect sang host khác tiếp tục loại header cấu hình.
- Hợp đồng dữ liệu/API: giữ envelope schema v1; API trả các trường collection health công khai, không trả generation/revision nội bộ. UI đọc bốn trường health đang cần từ `GET /sources`, hiển thị giờ Việt Nam, xử lý trường thiếu/lạ và không gọi endpoint job.
- Tenant/quyền/quota: kiểm tra ownership phía server; Viewer chỉ đọc/làm mới. Không mở rộng quyền ứng dụng. Normalizer bổ sung `dynamodb:ConditionCheckItem` chỉ trên ControlTable cho transaction kiểm tra generation; không thêm tài nguyên SAM hoặc dependency runtime.

## Cách kiểm tra

Kết quả dưới đây đã chạy local trong cùng phiên làm việc; không cộng các nhóm test để gọi là toàn bộ suite:

| Kiểm tra | Kết quả / bằng chứng |
| --- | --- |
| Nhóm backend sau sửa lỗi | 146 test được chọn pass, gồm lifecycle/count concurrency, Normalizer, migration, ingestion, pipeline và snapshot; kết quả terminal trong phiên làm việc, không có một JUnit tổng cho nhóm này. |
| Hai ca từng tái hiện lỗi | Job obsolete kết thúc và migration/reimport không tăng observation: đều pass sau sửa, chỉ dùng Moto. |
| Audit bổ sung Python 3.12 | 176 pass; `build/evidence/huong-audit-20261106.xml`. |
| SAM/IAM khi chuẩn bị package | 18 pass; `build/evidence/huong-package-infra-20261106.xml`. Nhóm này có phần trùng với audit 176 test. |
| Frontend trang Nguồn | 7 test cấu hình/reload/409 và 8 test health pass, chạy với `--maxWorkers=1`; kết quả terminal và preview fixture local. |
| Frontend production build | Đạt; còn cảnh báo bundle JavaScript lớn. |
| Ruff / diff whitespace | Đạt; có thông báo chuyển LF/CRLF từ Git trên một số tài liệu. |
| Package Python 3.12 | 43/43 file staging khớp source; 8 dependency runtime pin theo lock và cài offline từ cache. ZIP giải nén/CRC/hash đạt; 259 file Python compile; 8 handler cho 9 function import isolated khi chặn socket/DNS. |
| SAM/CloudFormation offline | Dịch được 103 tài nguyên; cfn-lint đạt cho hai template nguồn, bản dịch và template local trỏ ZIP. |

Lệnh kiểm tra có thể dùng cho phần code sau khi checkout đúng revision và chuẩn bị dependency local:

```powershell
build/venv312/Scripts/python.exe -m pytest -p no:cacheprovider -q tests/test_obsolete_normalizer_jobs.py tests/test_migrate_observation_ids.py tests/test_source_lifecycle.py tests/test_job_counts_concurrency.py
```

Từ `apps/web`:

```powershell
node ./node_modules/vitest/vitest.mjs run src/pages/Sources/SourceListPage.test.jsx src/pages/Sources/SourceListPage.health.test.jsx --maxWorkers=1
npm run build
```

Lệnh là hướng dẫn tái kiểm tra; chưa chạy lại chúng trong bước soạn mô tả PR. Kiểm thử đầy đủ trên CI và xác nhận reviewer còn chờ.

## Checklist

- [x] Rà soát diff thuộc phạm vi tính năng; test/fixture dùng dữ liệu giả, không đưa credential thật hoặc dữ liệu khách hàng vào bằng chứng.
- [x] Toàn bộ công việc mới dùng local/Moto/cache; không deploy/upload, gọi AWS/AI hoặc bật lịch. Không phát sinh sử dụng dịch vụ trả phí từ các bước này.
- [x] Tenant được xác định và kiểm tra phía máy chủ; có test ownership/tenant và Viewer.
- [x] Tài liệu contract, UI health và kết quả package local đã cập nhật.
- [x] Nêu rõ giới hạn và việc còn chờ.
- [ ] Minh xác nhận UI và thông điệp thu thập/chuẩn hóa trên bản tích hợp.
- [ ] Hiếu xác nhận contract API, generation, 409 và quyền truy cập.
- [ ] Hoàng xác nhận IAM/package và kiểm chứng SAM/runtime Linux local.
- [ ] CI toàn bộ suite đạt trên revision chốt.

## Giới hạn và bàn giao artifact

Package đã kiểm tra trên Windows/Python 3.12; chưa chạy `sam build` hoặc container Lambda Linux, chưa nghiệm thu AWS trên revision này. Migration không được thực thi với dữ liệu thật. Quy trình chi phí/phê duyệt của dự án vẫn áp dụng trước mọi thao tác dịch vụ thật.

Artifact local `build/lambda-package-20261106.zip` có SHA-256 `b81685071771da96080be25cea7b6db8566d01d9455ee3b3c760594355368fb0`. Manifest `build/evidence/huong-package-manifest-20261106.json` ghi hash từng file, dependency/wheel và giới hạn kiểm tra. Thư mục build bị Git ignore: artifact, JUnit và ảnh cần bàn giao riêng qua kênh đã được nhóm thống nhất, không tự có trên GitHub. Bản mô tả này chưa tạo PR hoặc gửi cho reviewer.
