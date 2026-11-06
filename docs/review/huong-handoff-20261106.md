# Bản bàn giao review của Hướng — 06/11/2026

Đã rà soát diff và phân nhóm để review code, UI và package local. Bản nháp mô tả PR ở [huong-pr-description-20261106.md](huong-pr-description-20261106.md). Chưa thay đổi Git index, tạo commit/branch, push, mở PR hoặc gửi thông báo cho thành viên nhóm trong bước này.

## Mốc so sánh và phạm vi PR

- HEAD/nhánh làm việc local: `huong` tại `55f0f3818f8089d1b69adc3b2fd93387f9e2901b` — Fix source handling bugs.
- Base `main` local: `e4dc86713b2d20eea3b36a3b4e9aa695f9136b8c` — Add source health to the API.
- `main..HEAD` gồm một commit, 27 file, đã có trước các bản sửa tiếp theo trong phiên làm việc. Nếu PR vào main local này, phạm vi bao gồm cả generation, quota/concurrency, validation enabled và bảo vệ HTTPS trong commit 55f0f38.
- Bản mô tả PR đi kèm mô tả toàn bộ phạm vi vào main; patch sau HEAD chỉ là phần bổ sung, không tự đầy đủ nếu áp thẳng lên main.
- Chưa xác minh lại remote trong bước chuẩn bị này. Khi tạo PR cần đối chiếu tip/base trên GitHub, cập nhật hoặc rebase và kiểm tra lại nếu có thay đổi. Không coi SHA local là trạng thái remote hiện tại.

## Nhóm thay đổi sau HEAD được đề xuất đưa vào review

| Nhóm | File | Nội dung |
| --- | --- | --- |
| A — Backend | `marketdata/repository.py`, `services/normalizer/app.py` | Kết thúc active job obsolete, counts có điều kiện và retry khi ghi lỗi; pin identity trong snapshot. |
| A — Migration | `scripts/migrate_observation_ids.py`, `tests/test_migrate_observation_ids.py` | Giữ ID hiện hành/generation, nhận diện ID legacy, hồi quy migration/reimport. |
| A — Test/contract | `tests/test_obsolete_normalizer_jobs.py` (mới), `docs/data-contract.md`, `services/normalizer/README.md` | Kiểm tra CSV/API/Web, terminal replay, counts race, tenant/source và lỗi ghi. |
| B — UI | `apps/web/src/pages/Sources/SourceListPage.jsx`, `apps/web/src/styles.css`, `apps/web/src/mocks/data.js` | Nhãn health, lần chạy/thành công, mã lỗi, thông điệp chuẩn hóa và làm mới cho Viewer. |
| B — Test/bàn giao | `apps/web/src/pages/Sources/SourceListPage.health.test.jsx` (mới), `docs/source-health-integration-handoff.md` | Tám ca health, preview local và cập nhật việc cần nghiệm thu. |
| C — Hồ sơ local | `docs/huong-aws-readiness.md`, `docs/huong-work-audit-20261106.md` (mới) | Kết quả đối chiếu công việc, staging/package và giới hạn runtime/AWS. |
| C — Review | Hai file trong `docs/review/` của bản bàn giao này (mới) | Mô tả PR, ranh giới file, bằng chứng và checklist reviewer. |

Ba file test/tài liệu mới trong nhóm A/B/C và hai file review là untracked ở thời điểm chuẩn bị; `git diff` thông thường không tự đưa chúng vào patch. Gói review local đi kèm bổ sung chúng rõ ràng. Có thể tách nhóm A, B, C thành commit khi chốt; mô tả trên chưa tạo các commit đó.

## Các thay đổi có sẵn được giữ ngoài patch tính năng

Các file sau đã có thay đổi trước khi bắt đầu sửa lỗi/UI/package, giữ nguyên để chủ sở hữu review riêng:

- `.github/PULL_REQUEST_TEMPLATE.md`
- `CONTRIBUTING.md`
- `README.md`
- `SECURITY.md`
- `docs/aws-deploy-free-plan.md`
- `docs/team-work-split.md`
- `infra/aws/README.md`
- `AGENTS.md` (untracked)
- `docs/ke-hoach-hoang-ho-tro-huong.md` (untracked)
- `.idea/` (untracked, không đọc hoặc đóng gói cấu hình IDE)

Phần quy định chi phí/bảo mật trong các file này vẫn áp dụng cho công việc. Việc giữ chúng ngoài patch tính năng chỉ là phân loại phạm vi review, không hủy quy tắc hoặc xóa thay đổi. Chủ sở hữu quyết định đưa thành nhóm chính sách riêng; tránh dùng `git add .` để gom toàn workspace vào PR tính năng. Các link AGENTS có sẵn trong tài liệu cần được đối chiếu khi nhóm chốt việc đưa quy định vào Git.

## Checklist theo người review đề xuất

### Minh — UI

- [ ] Năm trạng thái và trường thiếu/lạ hiển thị rõ; bật/tắt không thay thế health.
- [ ] Lần chạy/thành công dùng giờ Việt Nam; lịch sử thành công được giữ khi retry/lỗi.
- [ ] Mã lỗi chỉ hiển thị an toàn khi phù hợp; thông điệp không đánh đồng thu thập với chuẩn hóa.
- [ ] Kiểm tra bảng cuộn ngang, quyền Admin/Analyst/Viewer và thao tác làm mới trên UI tích hợp.

### Hiếu — API/quyền/vòng đời nguồn

- [ ] Generation/revision do server quản lý; ownership tenant/source/job được giữ.
- [ ] POST/PATCH/DELETE và xung đột 409 giữ contract; Viewer không có quyền mutation/job mới.
- [ ] Job obsolete kết thúc với counts đúng, không làm mất trạng thái terminal hoặc lịch sử collection success.
- [ ] Migration giữ lịch sử các generation, legacy và các ID không đủ metadata; có kế hoạch riêng trước mọi migration dữ liệu thật.

### Hoàng — Runtime/IAM/package

- [ ] Quyền ConditionCheckItem chỉ trên ControlTable; kiểm tra transaction guard và quota concurrency.
- [ ] ZIP/hash/source identity/dependency khớp manifest, runtime code có đủ bản sửa.
- [ ] Kiểm chứng bằng SAM CLI hoặc runtime Linux local; isolated import trên Windows chưa thay thế bước này.
- [ ] Chốt điều kiện chi phí/phạm vi trước nghiệm thu AWS; không tự sử dụng credit/Free Tier làm phê duyệt.

Đây là phân công đề xuất, chưa có xác nhận review/approve và chưa gửi tin nhắn thay Hướng.

## Bằng chứng đã có và giới hạn

| Nội dung | Bằng chứng |
| --- | --- |
| 146 test backend + hai ca tái hiện | Kết quả terminal trong các bước sửa trước; không có JUnit tổng của nhóm 146. |
| Audit bổ sung Python 3.12 | `build/evidence/huong-audit-20261106.xml`: 176 test, 0 failure/error. |
| Package SAM/IAM | `build/evidence/huong-package-infra-20261106.xml`: 18 test, 0 failure/error. Có phần trùng nhóm audit; không cộng làm tổng coverage. |
| Frontend | 7 ca hiện có + 8 ca health pass qua terminal; production build đạt, cảnh báo bundle lớn còn tồn tại. |
| Preview | `build/evidence/source-health-ui-20261106.jpg`, dữ liệu giả. |
| Runtime ZIP | `build/lambda-package-20261106.zip`, SHA-256 `b81685071771da96080be25cea7b6db8566d01d9455ee3b3c760594355368fb0`. |
| Manifest package | `build/evidence/huong-package-manifest-20261106.json`: 43 staging file khớp source; tám dependency pure-Python; 8 handler cho 9 function import isolated. |
| SAM local | `build/lambda-package-20261106.sam.yaml`; dịch offline 103 tài nguyên; cfn-lint đạt cho bốn template. |
| Chi tiết build | `build/evidence/huong-package-review-20261106.md`, có lệnh và giới hạn. |

Không chạy lại toàn bộ pytest/Vitest trong bước rà soát này. CI toàn bộ suite, backend triển khai/JWT thật, runtime Linux và nghiệm thu AWS của revision cuối còn chờ. Không thêm tài nguyên, dependency runtime, dịch vụ trả phí hoặc chạy migration thật trong các thay đổi mới.

## Gói review local

Gói dự kiến: `build/review/huong-review-20261106.zip`, kèm SHA-256 và manifest bên cạnh. Nó chứa file nguồn thuộc scope review, hai patch và bản sao bằng chứng chọn lọc; không chứa runtime ZIP 17 MB hoặc wheel/cache. Runtime ZIP được bàn giao riêng cùng hash ở trên. Build/evidence bị Git ignore và không tự đi theo PR; khi cần chuyển artifact dùng kênh nhóm đã thống nhất, không đưa credential hoặc URL có chữ ký vào mô tả.

- `changes-since-55f0f38.patch`: thay đổi được chọn sau HEAD, gồm file mới; giữ ngoài các file chính sách/IDE có sẵn.
- `changes-vs-main-e4dc867.patch`: bản tính năng hoàn chỉnh so với main local, gồm commit 55f0f38 và các phần bổ sung được chọn.
- `manifest.json`: base/head, đường dẫn/hash file, danh sách loại trừ và hash artifact tham chiếu.

Patch được kiểm tra reverse-apply trên working tree hiện tại ở chế độ `--check`, không thay đổi file hoặc index. Trước áp dụng ở máy khác, người nhận phải xác minh base và chạy kiểm tra patch trên checkout sạch; không áp patch vào thư mục có sửa riêng chưa lưu. Mô tả PR chỉ sẵn sàng để người dùng kiểm tra; chưa phải PR đã đăng.
