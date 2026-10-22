import { Alert, Button, Result, Spin } from 'antd';
import { Navigate, Route, Routes } from 'react-router-dom';
import { useUser } from '../hooks/useUser.js';
import { WorkspaceLayout } from './WorkspaceLayout.jsx';
import { RoleGuard } from './RoleGuard.jsx';
import {
  CatalogPage,
  DashboardPage,
  MembersPage,
  ReportDetailPage,
  ReportsPage,
  SourcesPage,
  UploadPage,
} from '../pages/WorkspacePages.jsx';

export function AppRoutes({ cognitoUser, signOut }) {
  const { user, loading, isMock, error, retry } = useUser();

  if (loading) {
    return (
      <main className="app-loading" role="status">
        <Spin size="large" />
        <span>Đang tải hồ sơ và quyền truy cập…</span>
      </main>
    );
  }

  if (!user) {
    return (
      <main className="app-load-error">
        <Result
          status="warning"
          title="Chưa tải được hồ sơ ứng dụng"
          subTitle={error?.message || 'Kiểm tra API GET /me rồi thử lại.'}
          extra={<Button type="primary" onClick={retry}>Thử lại</Button>}
        />
      </main>
    );
  }

  return (
    <>
      {isMock && (
        <Alert
          className="preview-alert"
          banner
          type="warning"
          message="Đang xem giao diện bằng hồ sơ mẫu · Dữ liệu tenant và vai trò chưa đến từ backend."
        />
      )}
      <WorkspaceLayout
        user={user}
        cognitoUser={cognitoUser}
        signOut={signOut}
      >
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/sources" element={<SourcesPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/reports/:id" element={<ReportDetailPage />} />
          <Route element={<RoleGuard user={user} allowedRoles={['admin', 'analyst']} />}>
            <Route path="/upload" element={<UploadPage />} />
            <Route path="/catalog" element={<CatalogPage />} />
          </Route>
          <Route element={<RoleGuard user={user} allowedRoles={['admin']} />}>
            <Route path="/members" element={<MembersPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </WorkspaceLayout>
    </>
  );
}
