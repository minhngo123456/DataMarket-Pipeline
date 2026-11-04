import { Alert, Button, Card, Form, Input } from 'antd';
import { useState } from 'react';
import { apiErrorMessage } from '../hooks/useApiData.js';
import { marketDataApi } from '../services/marketDataApi.js';

function failureMessage(error) {
  if (error?.response?.status === 409) {
    return 'Tài khoản này đã thuộc một workspace hoặc đã bị khóa. Hãy liên hệ Admin của workspace đó.';
  }
  return apiErrorMessage(error, 'Không tạo được workspace.');
}

/** Shown to a signed-in account that belongs to no tenant yet. */
export function CreateWorkspace({ email, signOut, onChanged }) {
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState(null);

  const create = async ({ name }) => {
    setSaving(true);
    setFailure(null);
    try {
      await marketDataApi.createTenant({ name });
      onChanged();
    } catch (error) {
      setFailure(failureMessage(error));
      setSaving(false);
    }
  };

  return (
    <main className="app-load-error">
      <Card title="Tạo workspace của bạn" style={{ maxWidth: 480, width: '100%' }}>
        <p>
          Tài khoản {email && <strong>{email}</strong>} chưa thuộc workspace nào.
          Tạo workspace mới, bạn sẽ là Admin của workspace đó.
        </p>
        {failure && <Alert type="error" showIcon title={failure} style={{ marginBottom: 16 }} />}
        <Form layout="vertical" onFinish={create} requiredMark={false}>
          <Form.Item
            label="Tên workspace"
            name="name"
            rules={[{ required: true, whitespace: true, min: 2, max: 80, message: 'Nhập từ 2 đến 80 ký tự.' }]}
          >
            <Input placeholder="Ví dụ: Shop ABC" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={saving} block>Tạo workspace</Button>
        </Form>
        <p style={{ marginTop: 16 }}>
          Muốn vào workspace đã có? Nhờ Admin của workspace đó thêm email của bạn, rồi kiểm tra lại.
          Đừng tạo workspace mới, vì mỗi tài khoản chỉ thuộc một workspace.
        </p>
        <Button onClick={onChanged} disabled={saving} style={{ marginRight: 8 }}>Kiểm tra lại</Button>
        <Button type="text" onClick={signOut} disabled={saving}>Đăng xuất</Button>
      </Card>
    </main>
  );
}
