import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Form, Input, Modal, Popconfirm, Select, Switch, Table, Tag, message } from 'antd';
import { useState } from 'react';
import { LoadError, PageHeading } from '../../components/PageHeading.jsx';
import { apiErrorMessage, useApiData } from '../../hooks/useApiData.js';
import { mockSources } from '../../mocks/data.js';
import { marketDataApi } from '../../services/marketDataApi.js';
import { formatDateTime, timeAgo } from '../../utils/timeHelpers.js';

const KINDS = [
  { value: 'csv', label: 'CSV (tải tệp lên)' },
  { value: 'api', label: 'REST API' },
  { value: 'web', label: 'Trang web tĩnh' },
];
const MAX_SOURCES = 3;
const URL_PLACEHOLDER = { api: 'https://api.nha-cung-cap.vn/v1/prices', web: 'https://shop.nha-cung-cap.vn/bang-gia' };

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
  const [form] = Form.useForm();
  const kind = Form.useWatch('kind', form);
  const role = user?.role;
  const canEdit = role === 'admin' || role === 'analyst';
  const rows = sources.data?.data || [];
  const limit = Math.min(user?.tenant?.max_sources ?? MAX_SOURCES, MAX_SOURCES);

  const run = async (action, mockUpdate, success) => {
    try {
      if (sources.isMock) {
        sources.setData((data) => ({ ...data, data: mockUpdate(data.data) }));
      } else {
        await action();
        sources.reload();
      }
      message.success(success);
      return true;
    } catch (error) {
      if (error?.response?.status === 409) {
        sources.reload();
        message.warning('Thao tác xung đột với cấu hình nguồn hiện tại. Đang tải lại danh sách; hãy kiểm tra trước khi thử lại.');
      } else {
        message.error(apiErrorMessage(error, 'Không lưu được nguồn.'));
      }
      return false;
    }
  };

  const toggle = (source, enabled) => run(
    () => marketDataApi.updateSource(source.source_id, { enabled }),
    (list) => list.map((s) => (s.source_id === source.source_id ? { ...s, enabled, updated_at: new Date().toISOString() } : s)),
    enabled ? `Đã bật ${source.name}.` : `Đã tắt ${source.name}.`,
  );

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
    const { headers, ...values } = fields;
    setSaving(true);
    const payload = { ...values, enabled: true, ...(headers?.trim() ? { headers: parseHeaders(headers) } : {}) };
    const ok = await run(
      () => marketDataApi.createSource(payload),
      (list) => {
        const source = { ...payload, updated_at: new Date().toISOString() };
        return list.some((item) => item.source_id === source.source_id)
          ? list.map((item) => (item.source_id === source.source_id ? { ...item, ...source } : item))
          : [...list, source];
      },
      `Đã lưu ${values.name}.`,
    );
    setSaving(false);
    if (ok) {
      setOpen(false);
      form.resetFields();
    }
  };

  const columns = [
    { title: 'NGUỒN', dataIndex: 'name', key: 'name', render: (name, row) => <div className="table-primary"><strong>{name}</strong><span>{row.source_id}</span></div> },
    { title: 'LOẠI', dataIndex: 'kind', key: 'kind', render: (value) => <Tag>{String(value).toUpperCase()}</Tag> },
    { title: 'ĐỊA CHỈ', key: 'endpoint', ellipsis: true, render: (_, row) => row.endpoint || row.url || '—' },
    { title: 'CẬP NHẬT', dataIndex: 'updated_at', key: 'updated_at', render: (v) => <span title={formatDateTime(v)}>{timeAgo(v)}</span> },
    {
      title: 'BẬT',
      dataIndex: 'enabled',
      key: 'enabled',
      render: (enabled, row) => (canEdit
        ? <Switch checked={enabled !== false} onChange={(value) => toggle(row, value)} aria-label={`Bật/tắt ${row.name}`} />
        : <Tag color={enabled !== false ? 'green' : 'default'}>{enabled !== false ? 'Đang bật' : 'Đã tắt'}</Tag>),
    },
    ...(role === 'admin' ? [{
      title: '',
      key: 'delete',
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
        description={`Gói Pilot cho tối đa ${limit} nguồn. Nguồn API và web được thu thập 6 giờ một lần.`}
        actions={canEdit && <Button type="primary" icon={<PlusOutlined />} disabled={rows.length >= limit} onClick={() => setOpen(true)}>Thêm nguồn</Button>}
      />
      {sources.isMock && <Alert className="page-alert" type="warning" showIcon title="Chưa kết nối được API, trang đang hiển thị dữ liệu mẫu. Thay đổi chỉ có trên màn hình." />}
      {canEdit && rows.length >= limit && <Alert className="page-alert" type="info" showIcon title={`Đã dùng hết ${limit} nguồn của gói Pilot.`} />}
      <Card className="panel-card table-panel" variant="borderless">
        {sources.error ? <LoadError error={apiErrorMessage(sources.error)} onRetry={sources.reload} /> : (
          <Table columns={columns} dataSource={rows} rowKey="source_id" loading={sources.loading} pagination={false} scroll={{ x: 640 }} locale={{ emptyText: 'Chưa cấu hình nguồn nào.' }} />
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
              <Form.Item label="Header HTTP (JSON)" name="headers" extra={'Không bắt buộc. Ví dụ: {"User-Agent": "MarketData"}'} rules={[{ validator: validateHeaders }]}><Input.TextArea rows={3} /></Form.Item>
            </>
          )}
        </Form>
      </Modal>
    </>
  );
}
