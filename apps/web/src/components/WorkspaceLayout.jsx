import {
  BellOutlined,
  MenuOutlined,
  SearchOutlined,
  ShopOutlined,
} from '@ant-design/icons';
import { Avatar, Badge, Button, Drawer, Dropdown, Input, Layout, Tag, Tooltip } from 'antd';
import { useMemo, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar.jsx';

const { Sider, Header, Content } = Layout;

const titleByPath = [
  ['/dashboard', 'Tổng quan'],
  ['/sources', 'Nguồn dữ liệu'],
  ['/upload', 'Tải dữ liệu'],
  ['/catalog', 'Danh mục sản phẩm'],
  ['/reports', 'Báo cáo'],
  ['/members', 'Thành viên'],
];

function initials(value = '') {
  return value.split(/[\s.@_-]+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join('') || 'U';
}

export function WorkspaceLayout({ user, cognitoUser, signOut, children }) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const email = user?.claims?.email || cognitoUser?.signInDetails?.loginId || cognitoUser?.username || 'Người dùng';
  const tenantName = user?.tenant?.name || user?.tenant_name || user?.tenant_id || 'Tenant chưa xác định';
  const role = String(user?.role || 'viewer').toLowerCase();
  const pageTitle = useMemo(
    () => titleByPath.find(([path]) => location.pathname.startsWith(path))?.[1] || 'Tổng quan',
    [location.pathname],
  );
  const accountItems = [{ key: 'signout', label: 'Đăng xuất' }];

  const sidebar = (
    <>
      <div className="workspace-brand">
        <div className="workspace-brand-mark">M</div>
        {!collapsed && (
          <div className="workspace-brand-copy">
            <strong>MarketData</strong>
            <span>PIPELINE</span>
          </div>
        )}
      </div>
      <div className="workspace-tenant">
        <div className="tenant-icon"><ShopOutlined /></div>
        {!collapsed && <div className="tenant-copy"><small>KHÔNG GIAN LÀM VIỆC</small><strong title={tenantName}>{tenantName}</strong></div>}
      </div>
      <Sidebar role={role} onNavigate={() => setMobileOpen(false)} />
      {!collapsed && <div className="sidebar-bottom"><span className="sidebar-status-dot" /> Hệ thống hoạt động</div>}
    </>
  );

  return (
    <Layout className="workspace-shell">
      <Sider
        className="workspace-sider"
        width={252}
        collapsedWidth={78}
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        breakpoint="lg"
        onBreakpoint={(small) => setCollapsed(small)}
        trigger={null}
      >
        {sidebar}
        <button className="sider-toggle" type="button" aria-label={collapsed ? 'Mở rộng thanh điều hướng' : 'Thu gọn thanh điều hướng'} onClick={() => setCollapsed((value) => !value)}>
          <span>{collapsed ? '›' : '‹'}</span>
        </button>
      </Sider>

      <Layout className="workspace-main">
        <Header className="workspace-header">
          <div className="header-left">
            <Button className="mobile-menu-button" type="text" icon={<MenuOutlined />} onClick={() => setMobileOpen(true)} aria-label="Mở menu" />
            <div className="header-heading"><span>WORKSPACE</span><strong>{pageTitle}</strong></div>
          </div>
          <div className="header-actions">
            <Input className="global-search" prefix={<SearchOutlined />} placeholder="Tìm kiếm sản phẩm, báo cáo…" aria-label="Tìm kiếm" />
            <Tooltip title="Thông báo"><Button className="notification-button" type="text" icon={<Badge dot><BellOutlined /></Badge>} aria-label="Thông báo" /></Tooltip>
            <Dropdown menu={{ items: accountItems, onClick: ({ key }) => key === 'signout' && signOut?.() }} trigger={['click']}>
              <button className="profile-trigger" type="button">
                <Avatar className="profile-avatar">{initials(email)}</Avatar>
                <span className="profile-copy"><strong>{email.split('@')[0]}</strong><span>{role === 'admin' ? 'Quản trị viên' : role === 'analyst' ? 'Chuyên viên phân tích' : 'Người xem'}</span></span>
                <Tag className="role-tag">{role}</Tag>
              </button>
            </Dropdown>
          </div>
        </Header>
        <Content className="workspace-content">
          <div className="workspace-page">{children || <Outlet />}</div>
        </Content>
      </Layout>

      <Drawer className="mobile-nav-drawer" title="Điều hướng" placement="left" open={mobileOpen} onClose={() => setMobileOpen(false)} size={280}>
        <Sidebar role={role} onNavigate={() => setMobileOpen(false)} />
      </Drawer>
    </Layout>
  );
}
