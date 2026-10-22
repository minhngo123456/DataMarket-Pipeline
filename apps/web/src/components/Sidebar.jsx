import {
  AppstoreOutlined,
  BarChartOutlined,
  CloudUploadOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import { Menu } from 'antd';
import { useLocation, useNavigate } from 'react-router-dom';

const items = [
  { key: '/dashboard', label: 'Tổng quan', icon: <AppstoreOutlined />, roles: ['admin', 'analyst', 'viewer'] },
  { key: '/sources', label: 'Nguồn dữ liệu', icon: <DatabaseOutlined />, roles: ['admin', 'analyst', 'viewer'] },
  { key: '/upload', label: 'Tải dữ liệu', icon: <CloudUploadOutlined />, roles: ['admin', 'analyst'] },
  { key: '/catalog', label: 'Danh mục sản phẩm', icon: <FileSearchOutlined />, roles: ['admin', 'analyst'] },
  { key: '/reports', label: 'Báo cáo', icon: <BarChartOutlined />, roles: ['admin', 'analyst', 'viewer'] },
  { key: '/members', label: 'Thành viên', icon: <TeamOutlined />, roles: ['admin'] },
];

export function Sidebar({ role, onNavigate }) {
  const location = useLocation();
  const navigate = useNavigate();
  const current = location.pathname.startsWith('/reports/') ? '/reports' : location.pathname;
  const visibleItems = items
    .filter((item) => item.roles.includes(String(role || '').toLowerCase()))
    .map(({ key, label, icon }) => ({ key, label, icon }));

  return (
    <Menu
      className="workspace-menu"
      mode="inline"
      selectedKeys={[current]}
      items={visibleItems}
      onClick={({ key }) => {
        navigate(key);
        onNavigate?.();
      }}
    />
  );
}
