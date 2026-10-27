import { PlusOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Form, Input, Modal, Select, Switch, Table, Tag, message } from 'antd';
import { useState } from 'react';
import { LoadError, PageHeading } from '../../components/PageHeading.jsx';
import { apiErrorMessage, useApiData } from '../../hooks/useApiData.js';
import { mockMembers } from '../../mocks/data.js';
import { marketDataApi } from '../../services/marketDataApi.js';

const ROLES = [
  { value: 'admin', label: 'Admin · toàn quyền' },
  { value: 'analyst', label: 'Analyst · tải và duyệt dữ liệu' },
  { value: 'viewer', label: 'Viewer · chỉ xem' },
];

export function MemberListPage({ user }) {
  const members = useApiData(() => marketDataApi.getMembers(), mockMembers);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();
  const rows = members.data?.data || [];
  const maxMembers = members.data?.max_members ?? 5;

  const update = async (member, changes) => {
    try {
      const result = members.isMock ? changes : await marketDataApi.updateMember(member.user_id, changes);
      members.setData((data) => ({ ...data, data: data.data.map((m) => (m.user_id === member.user_id ? { ...m, ...changes, ...result } : m)) }));
      message.success(`Đã cập nhật ${member.email || member.user_id}.`);
    } catch (error) {
      message.error(apiErrorMessage(error, 'Không cập nhật được thành viên.'));
    }
  };

  const invite = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      const result = members.isMock ? { ...values, status: 'active' } : await marketDataApi.inviteMember(values);
      members.setData((data) => ({ ...data, data: [...data.data, { user_id: result.user_id || values.email, ...values, status: result.status || 'active' }], total: (data.total || 0) + 1 }));
      message.success(result.message || `Đã thêm ${values.email}.`);
      setOpen(false);
      form.resetFields();
      if (!members.isMock) members.reload();
    } catch (error) {
      message.error(apiErrorMessage(error, 'Không mời được thành viên.'));
    } finally {
      setSaving(false);
    }
  };

  const columns = [
    { title: 'THÀNH VIÊN', key: 'member', render: (_, row) => <div className="table-primary"><strong>{row.email || row.user_id}</strong><span>{row.user_id}{row.user_id === user?.user_id && ' · bạn'}</span></div> },
    {
      title: 'VAI TRÒ',
      dataIndex: 'role',
      key: 'role',
      render: (role, row) => (row.user_id === user?.user_id
        ? <Tag color="blue">{role}</Tag>
        : <Select size="small" value={role} style={{ width: 200 }} options={ROLES} onChange={(value) => update(row, { role: value })} aria-label={`Vai trò của ${row.email || row.user_id}`} />),
    },
    {
      title: 'HOẠT ĐỘNG',
      dataIndex: 'status',
      key: 'status',
      render: (status, row) => (row.user_id === user?.user_id
        ? <Tag color="green">active</Tag>
        : <Switch checked={status === 'active'} checkedChildren="Bật" unCheckedChildren="Khóa" onChange={(on) => update(row, { status: on ? 'active' : 'disabled' })} />),
    },
  ];

  return (
    <>
      <PageHeading
        eyebrow="QUẢN TRỊ WORKSPACE"
        title="Thành viên"
        description={`${rows.length}/${maxMembers} thành viên. Bạn không tự đổi vai trò hay khóa chính mình được.`}
        actions={<Button type="primary" icon={<PlusOutlined />} disabled={rows.length >= maxMembers} onClick={() => setOpen(true)}>Mời thành viên</Button>}
      />
      {members.isMock && <Alert className="page-alert" type="warning" showIcon title="Chưa kết nối được API, trang đang hiển thị dữ liệu mẫu. Thay đổi chỉ có trên màn hình." />}
      <Card className="panel-card table-panel" variant="borderless">
        {members.error ? <LoadError error={apiErrorMessage(members.error)} onRetry={members.reload} /> : (
          <Table columns={columns} dataSource={rows} rowKey="user_id" loading={members.loading} pagination={false} scroll={{ x: 640 }} />
        )}
      </Card>
      <Modal title="Mời thành viên" open={open} okText="Thêm" cancelText="Hủy" confirmLoading={saving} onOk={invite} onCancel={() => setOpen(false)} destroyOnHidden>
        <p className="modal-description">Tài khoản Cognito của người này do admin tạo; ở đây chỉ cấp quyền vào workspace.</p>
        <Form form={form} layout="vertical" initialValues={{ role: 'analyst' }}>
          <Form.Item label="Email" name="email" rules={[{ required: true, type: 'email', message: 'Nhập email hợp lệ.' }]}><Input placeholder="ten@congty.com" /></Form.Item>
          <Form.Item label="Vai trò" name="role" rules={[{ required: true }]}><Select options={ROLES} /></Form.Item>
        </Form>
      </Modal>
    </>
  );
}
