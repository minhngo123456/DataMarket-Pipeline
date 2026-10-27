import { RightOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Table, Tag } from 'antd';
import { useNavigate } from 'react-router-dom';
import { LoadError, PageHeading } from '../../components/PageHeading.jsx';
import { apiErrorMessage, useApiData } from '../../hooks/useApiData.js';
import { mockReports } from '../../mocks/data.js';
import { marketDataApi } from '../../services/marketDataApi.js';
import { formatDateTime } from '../../utils/timeHelpers.js';

export function ReportStatus({ report }) {
  if (report.status === 'ready' && report.ai_status !== 'fallback_quantitative') return <Tag color="green">Hoàn tất</Tag>;
  if (report.status === 'partial' || report.ai_status === 'fallback_quantitative') return <Tag color="orange">AI lỗi, chỉ có số liệu</Tag>;
  if (report.status === 'queued' || report.status === 'pending') return <Tag color="blue">Đang tạo</Tag>;
  return <Tag>{report.status || 'Không rõ'}</Tag>;
}

export function ReportListPage() {
  const navigate = useNavigate();
  const reports = useApiData(() => marketDataApi.getReports({ limit: 30 }), mockReports);
  const rows = [...(reports.data?.data || [])].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  const columns = [
    { title: 'KỲ BÁO CÁO', dataIndex: 'period', key: 'period', render: (period, row) => <div className="table-primary"><strong>{period || '—'}</strong><span>{row.report_id}</span></div> },
    { title: 'TẠO LÚC', dataIndex: 'created_at', key: 'created_at', render: formatDateTime },
    { title: 'TRẠNG THÁI', key: 'status', render: (_, row) => <ReportStatus report={row} /> },
    { title: '', key: 'open', render: (_, row) => <Button type="text" onClick={() => navigate(`/reports/${row.report_id}`)}>Xem <RightOutlined /></Button> },
  ];
  return (
    <>
      <PageHeading eyebrow="BÁO CÁO AI" title="Báo cáo" description="Báo cáo thị trường hằng ngày lúc 08:00 (giờ Việt Nam), tóm tắt bởi Claude." />
      {reports.isMock && <Alert className="page-alert" type="warning" showIcon title="Chưa kết nối được API, trang đang hiển thị dữ liệu mẫu." />}
      <Card className="panel-card table-panel" variant="borderless">
        {reports.error ? <LoadError error={apiErrorMessage(reports.error)} onRetry={reports.reload} /> : (
          <Table columns={columns} dataSource={rows} rowKey="report_id" loading={reports.loading} pagination={{ pageSize: 20, hideOnSinglePage: true }} locale={{ emptyText: 'Chưa có báo cáo nào.' }} scroll={{ x: 600 }} />
        )}
      </Card>
    </>
  );
}
