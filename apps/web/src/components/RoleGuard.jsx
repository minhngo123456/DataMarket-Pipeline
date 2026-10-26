import { Result } from 'antd';
import { Link, Outlet } from 'react-router-dom';

export function RoleGuard({ user, allowedRoles }) {
  const role = String(user?.role || '').toLowerCase();
  const allowed = allowedRoles.includes(role);

  return allowed ? <Outlet /> : (
    <Result
      status="403"
      title="403 · Không có quyền truy cập"
      subTitle={`Vai trò “${role || 'chưa xác định'}” không được phép mở trang này.`}
      extra={<Link className="ant-btn ant-btn-primary" to="/dashboard">Về trang tổng quan</Link>}
    />
  );
}
