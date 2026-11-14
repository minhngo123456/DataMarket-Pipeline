import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Form, Input, Modal, Popconfirm, Select, Switch, Table, Tag, message } from 'antd';
import { useEffect, useState } from 'react';
import { LoadError, PageHeading } from '../../components/PageHeading.jsx';
import { apiErrorMessage, useApiData } from '../../hooks/useApiData.js';
import { mockSources } from '../../mocks/data.js';
import { marketDataApi } from '../../services/marketDataApi.js';
import { formatDateTime, timeAgo } from '../../utils/timeHelpers.js';

const KINDS = [
  { value: 'csv', label: 'CSV (tải tệp lên)' },
  { value: 'api', label: 'REST API' },
  { value: 'web', label: 'Trang web' },
];
const MAX_SOURCES = 3;
const URL_PLACEHOLDER = { api: 'https://api.nha-cung-cap.vn/v1/prices', web: 'https://phuchy.vn/' };
const COLLECTION_STATUS = {
  queued: { color: 'processing', label: 'Chờ xử lý' },
  running: { color: 'processing', label: 'Đang thu thập' },
  retrying: { color: 'warning', label: 'Chờ thử lại' },
  succeeded: { color: 'success', label: 'Đã thu thập' },
  failed: { color: 'error', label: 'Thu thập lỗi' },
};

function CollectionStatus({ source }) {
  const status = source.collection_status;
  const display = Object.hasOwn(COLLECTION_STATUS, status)
    ? COLLECTION_STATUS[status]
    : { color: 'default', label: status ? 'Chưa xác định' : 'Chưa thu thập' };
  const error = source.last_error_code;
  const showError = (status === 'retrying' || status === 'failed')
    && typeof error === 'string' && /^[a-z][a-z0-9_]{0,95}$/.test(error);
  return (
    <div className="source-collection-status">
      <Tag color={display.color}>{display.label}</Tag>
      {showError && <span className="source-collection-error">Mã lỗi: <code>{error}</code></span>}
    </div>
  );
}

function CollectionTime({ value }) {
  if (typeof value !== 'string' || !value.trim() || !Number.isFinite(Date.parse(value))) {
    return <span className="muted">Chưa ghi nhận</span>;
  }
  return <time dateTime={value} title="Giờ Việt Nam (UTC+7)">{formatDateTime(value)}</time>;
}

function CollectionHistory({ source }) {
  return (
    <dl className="source-collection-history">
      <div><dt>Lần chạy</dt><dd><CollectionTime value={source.last_attempt_at} /></dd></div>
      <div><dt>Thành công gần nhất</dt><dd><CollectionTime value={source.last_success_at} /></dd></div>
    </dl>
  );
}

function parseHeaders(text) {
  const parsed = JSON.parse(text);
  const valid = parsed && typeof parsed === 'object' && !Array.isArray(parsed) && Object.values(parsed).every((v) => typeof v === 'string');
  if (!valid) throw new Error('invalid headers');
  return parsed;
}

const validateHeaders = async (_, text) => {
  if (!text?.trim()) return;
  try {
    parseHeaders(text);
  } catch {
    throw new Error('Nhập JSON dạng {"Tên-header": "giá trị"}.');
  }
};

export function SourceListPage({ user }) {
  const sources = useApiData(() => marketDataApi.getSources(), mockSources);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [collecting, setCollecting] = useState(null);
  const [pollUntil, setPollUntil] = useState(() => Date.now() + 120000);
  const [form] = Form.useForm();
  const kind = Form.useWatch('kind', form);
  const role = user?.role;
  const canEdit = role === 'admin' || role === 'analyst';
  const rows = sources.data?.data || [];
  const limit = Math.min(user?.tenant?.max_sources ?? MAX_SOURCES, MAX_SOURCES);

  useEffect(() => {
    if (sources.isMock || sources.loading || sources.error || Date.now() >= pollUntil
      || !(sources.data?.data || []).some((s) => ['queued', 'running', 'retrying'].includes(s.collection_status))) return undefined;
    const timer = setTimeout(sources.reload, 3000);
    return () => clearTimeout(timer);
  }, [sources.data, sources.loading, sources.error, sources.isMock, sources.reload, pollUntil]);

  const run = async (action, mockUpdate, success, collectExpected = false) => {
    let result;
    try {
      if (sources.isMock) {
        sources.setData((data) => ({ ...data, data: mockUpdate(data.data) }));
      } else {
        result = await action();
        if (collectExpected && (!result?.job_id || result.collection_status !== 'queued')) {
          sources.reload();
          throw new Error('Nguồn đã lưu nhưng backend chưa xác nhận job thu thập. Cần cập nhật API và template trước khi thử lại.');
        }
        if (result?.job_id) setPollUntil(Date.now() + 120000);
        sources.reload();
      }
      message.success(result?.collection_status === 'queued' ? 'Đã gửi yêu cầu thu thập ngay. Trạng thái sẽ tự cập nhật.' : success);
      return true;
    } catch (error) {
      if (error?.response?.status === 409) {
        sources.reload();
        message.warning('Thao tác xung đột với cấu hình nguồn hiện tại. Đang tải lại danh sách; hãy kiểm tra trước khi thử lại.');
      } else {
        message.error(apiErrorMessage(error, 'Không lưu được nguồn.'));
        if (error?.response?.data?.error?.details?.source_saved) sources.reload();
      }
      return false;
    }
  };

  const toggle = (source, enabled) => run(
    () => marketDataApi.updateSource(source.source_id, { enabled, ...(enabled && ['api', 'web'].includes(source.kind) ? { collect_now: true } : {}) }),
    (list) => list.map((s) => (s.source_id === source.source_id ? { ...s, enabled, updated_at: new Date().toISOString() } : s)),
    enabled ? `Đã bật ${source.name}.` : `Đã tắt ${source.name}.`,
    enabled && ['api', 'web'].includes(source.kind),
  );

  const collect = async (source) => {
    setCollecting(source.source_id);
    await run(
      () => marketDataApi.updateSource(source.source_id, { collect_now: true }),
      (list) => list,
      'Đã gửi yêu cầu thu thập.',
      true,
    );
    setCollecting(null);
  };

  const remove = (source) => run(
    () => marketDataApi.deleteSource(source.source_id),
    (list) => list.filter((s) => s.source_id !== source.source_id),
    `Đã xóa ${source.name}.`,
  );

  const create = async () => {
    let fields;
    try {
      fields = await form.validateFields();
    } catch {
      return; // antd already shows the field errors
    }
    const { headers, price_basis, ...values } = fields;
    setSaving(true);
    const payload = { ...values, enabled: true, ...(['api', 'web'].includes(values.kind) ? { collect_now: true } : {}), ...(values.kind === 'web' ? { price_basis } : {}), ...(headers?.trim() ? { headers: parseHeaders(headers) } : {}) };
    const ok = await run(
      () => marketDataApi.createSource(payload),
      (list) => {
        const source = { ...payload, updated_at: new Date().toISOString() };
        return list.some((item) => item.source_id === source.source_id)
          ? list.map((item) => (item.source_id === source.source_id ? { ...item, ...source } : item))
          : [...list, source];
      },
      `Đã lưu ${values.name}.`,
      payload.collect_now,
    );
    setSaving(false);
    if (ok) {
      setOpen(false);
      form.resetFields();
    }
  };

  const columns = [
    { title: 'NGUỒN', dataIndex: 'name', key: 'name', width: 200, render: (name, row) => <div className="table-primary"><strong>{name}</strong><span>{row.source_id}</span></div> },
    { title: 'LOẠI', dataIndex: 'kind', key: 'kind', width: 90, render: (value) => <Tag>{String(value).toUpperCase()}</Tag> },
    { title: 'ĐỊA CHỈ', key: 'endpoint', width: 190, ellipsis: true, render: (_, row) => row.endpoint || row.url || '—' },
    { title: 'THU THẬP', key: 'collection', width: 220, render: (_, row) => <CollectionStatus source={row} /> },
    { title: 'LỊCH SỬ THU THẬP', key: 'collection_history', width: 190, render: (_, row) => <CollectionHistory source={row} /> },
    { title: 'SỬA CẤU HÌNH', dataIndex: 'updated_at', key: 'updated_at', width: 140, render: (v) => <span title={formatDateTime(v)}>{timeAgo(v)}</span> },
    ...(canEdit ? [{
      title: 'THU THẬP', key: 'collect_now', width: 150,
      render: (_, row) => ['api', 'web'].includes(row.kind) && row.enabled !== false ? (
        <Button size="small" loading={collecting === row.source_id} disabled={['queued', 'running', 'retrying'].includes(row.collection_status)} onClick={() => collect(row)} aria-label={`Thu thập ${row.name}`}>Thu thập ngay</Button>
      ) : null,
    }] : []),
    {
      title: 'BẬT',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 100,
      render: (enabled, row) => (canEdit
        ? <Switch checked={enabled !== false} onChange={(value) => toggle(row, value)} aria-label={`Bật/tắt ${row.name}`} />
        : <Tag color={enabled !== false ? 'green' : 'default'}>{enabled !== false ? 'Đang bật' : 'Đã tắt'}</Tag>),
    },
    ...(role === 'admin' ? [{
      title: '',
      key: 'delete',
      width: 56,
      render: (_, row) => (
        <Popconfirm title={`Xóa nguồn ${row.name}?`} description="Cấu hình nguồn bị xóa, dữ liệu giá đã lưu vẫn giữ." okText="Xóa" cancelText="Hủy" okButtonProps={{ danger: true }} onConfirm={() => remove(row)}>
          <Button type="text" danger icon={<DeleteOutlined />} aria-label={`Xóa ${row.name}`} />
        </Popconfirm>
      ),
    }] : []),
  ];

  return (
    <>
      <PageHeading
        eyebrow="KẾT NỐI VÀ ĐỒNG BỘ"
        title="Nguồn dữ liệu"
        description={`Gói Pilot cho tối đa ${limit} nguồn. API và web được thu thập ngay khi lưu hoặc bật nguồn, sau đó theo lịch 6 giờ.`}
        actions={<>
          <Button icon={<ReloadOutlined />} loading={sources.loading} disabled={sources.loading} onClick={sources.reload}>Làm mới</Button>
          {canEdit && <Button type="primary" icon={<PlusOutlined />} disabled={rows.length >= limit} onClick={() => setOpen(true)}>Thêm nguồn</Button>}
        </>}
      />
      {sources.isMock && <Alert className="page-alert" type="warning" showIcon title="Chưa kết nối được API, trang đang hiển thị dữ liệu mẫu. Thay đổi chỉ có trên màn hình." />}
      {canEdit && rows.length >= limit && <Alert className="page-alert" type="info" showIcon title={`Đã dùng hết ${limit} nguồn của gói Pilot.`} />}
      <p className="source-collection-note">“Đã thu thập” nghĩa là đã đọc nguồn và gửi dữ liệu để chuẩn hóa, chưa xác nhận các dòng đã được chấp nhận. Thời gian hiển thị theo giờ Việt Nam (UTC+7).</p>
      <Card className="panel-card table-panel" variant="borderless">
        {sources.error ? <LoadError error={apiErrorMessage(sources.error)} onRetry={sources.reload} /> : (
          <Table columns={columns} dataSource={rows} rowKey="source_id" loading={sources.loading} pagination={false} scroll={{ x: 1336 }} locale={{ emptyText: 'Chưa cấu hình nguồn nào.' }} />
        )}
      </Card>
      <Modal title="Thêm nguồn dữ liệu" open={open} okText="Thêm" cancelText="Hủy" confirmLoading={saving} onOk={create} onCancel={() => setOpen(false)} destroyOnHidden>
        <Form form={form} layout="vertical" initialValues={{ kind: 'csv' }}>
          <Form.Item label="Tên hiển thị" name="name" rules={[{ required: true, message: 'Nhập tên nguồn.' }]}><Input placeholder="Website đối thủ B" /></Form.Item>
          <Form.Item label="Mã nguồn" name="source_id" extra="Chữ thường, số, gạch ngang. Không đổi được sau khi tạo." rules={[{ required: true, message: 'Nhập mã nguồn.' }, { pattern: /^[a-z0-9_-]{2,40}$/, message: 'Chỉ dùng a-z, 0-9, - và _ (2 đến 40 ký tự).' }]}><Input placeholder="competitor-b" /></Form.Item>
          <Form.Item label="Loại" name="kind" rules={[{ required: true }]}><Select options={KINDS} /></Form.Item>
          {(kind === 'api' || kind === 'web') && (
            <>
              <Form.Item label="Địa chỉ URL" name="endpoint" rules={[{ required: true, message: 'Nhập địa chỉ URL.' }, { pattern: /^https?:\/\/[^\s/]+\S*$/i, message: 'URL phải bắt đầu bằng http:// hoặc https://.' }]}><Input placeholder={URL_PLACEHOLDER[kind]} /></Form.Item>
              {kind === 'web' && <>
                <Alert type="info" showIcon title="Nhập URL trang chủ, danh mục hoặc sản phẩm. Hệ thống đọc bảng giá, dữ liệu Product và tối đa 10 liên kết sản phẩm cùng website; hỗ trợ thêm nguồn JSON công khai của Haravan/Shopify. Trang cần đăng nhập, chặn bot hoặc chỉ có JavaScript riêng có thể chưa hỗ trợ. SKU mới cần được duyệt trong Catalog." />
                <Form.Item label="Cách tính thuế của giá nguồn" name="price_basis" preserve={false} extra="Chọn theo thông tin của người bán. Chưa xác định thì giá chưa vào bảng so sánh." rules={[{ required: true, message: 'Chọn cách tính thuế của giá nguồn.' }]}>
                  <Select options={[
                    { value: 'tax_included', label: 'Giá đã gồm thuế' },
                    { value: 'tax_excluded', label: 'Giá chưa gồm thuế' },
                    { value: 'unknown', label: 'Chưa xác định' },
                  ]} />
                </Form.Item>
              </>}
              <Form.Item label="Header HTTP (JSON)" name="headers" extra={'Không bắt buộc. Ví dụ: {"User-Agent": "MarketData"}'} rules={[{ validator: validateHeaders }]}><Input.TextArea rows={3} /></Form.Item>
            </>
          )}
        </Form>
      </Modal>
    </>
  );
}
