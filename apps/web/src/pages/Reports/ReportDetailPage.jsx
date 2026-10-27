import { DownloadOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Col, Row, Spin, Table } from 'antd';
import ReactMarkdown from 'react-markdown';
import { Link, useParams } from 'react-router-dom';
import { LoadError, PageHeading } from '../../components/PageHeading.jsx';
import { apiErrorMessage, useApiData } from '../../hooks/useApiData.js';
import { mockReportDetails } from '../../mocks/data.js';
import { marketDataApi } from '../../services/marketDataApi.js';
import { formatPercent } from '../../utils/priceFormat.js';
import { formatDateTime } from '../../utils/timeHelpers.js';
import { ReportStatus } from './ReportListPage.jsx';

export function ReportDetailPage() {
  const { id } = useParams();
  const detail = useApiData(
    () => marketDataApi.getReport(id),
    () => {
      if (!mockReportDetails[id]) throw new Error('not found');
      return mockReportDetails[id];
    },
    [id],
  );
  const report = detail.data;
  const aiFailed = report && (report.ai_status === 'fallback_quantitative' || report.status === 'partial');

  if (detail.loading) return <div className="center-block"><Spin /></div>;
  if (detail.error || !report) {
    return <><div className="back-link"><Link to="/reports">← Quay lại báo cáo</Link></div><LoadError error={detail.error?.response?.status === 404 ? 'Không tìm thấy báo cáo.' : apiErrorMessage(detail.error)} onRetry={detail.reload} /></>;
  }

  const metrics = report.metrics || {};
  return (
    <>
      <div className="back-link"><Link to="/reports">← Quay lại báo cáo</Link></div>
      <PageHeading
        eyebrow={report.report_id}
        title={`Báo cáo ${report.period || ''}`}
        description={`Tạo lúc ${formatDateTime(report.created_at)}`}
        actions={report.download_url && <Button icon={<DownloadOutlined />} href={report.download_url} target="_blank" rel="noreferrer">Tải JSON gốc</Button>}
      />
      {detail.isMock && <Alert className="page-alert" type="warning" showIcon title="Chưa kết nối được API, đây là báo cáo mẫu." />}
      {aiFailed && <Alert className="page-alert" type="warning" showIcon title="AI không khả dụng trong kỳ này" description="Claude API lỗi nên báo cáo chỉ có số liệu thống kê, không có phần phân tích." />}
      <Row gutter={[16, 16]}>
        <Col xs={24} md={6}><Card className="mini-stat" variant="borderless"><span>Trạng thái</span><strong className="mini-stat-text"><ReportStatus report={report} /></strong></Card></Col>
        <Col xs={24} md={6}><Card className="mini-stat" variant="borderless"><span>Sản phẩm</span><strong>{metrics.total_products ?? '—'}</strong></Card></Col>
        <Col xs={24} md={6}><Card className="mini-stat" variant="borderless"><span>Lượt quan sát giá</span><strong>{metrics.total_observations ?? '—'}</strong></Card></Col>
        <Col xs={24} md={6}><Card className="mini-stat" variant="borderless"><span>Chênh lệch bất thường</span><strong className={metrics.anomaly_count ? 'text-amber' : ''}>{metrics.anomaly_count ?? 0}</strong></Card></Col>
      </Row>
      <Card className="panel-card report-body" variant="borderless" title="Phân tích">
        {report.summary && !aiFailed ? <ReactMarkdown>{report.summary}</ReactMarkdown> : <p className="empty-note">Không có nội dung AI. Xem số liệu ở trên.</p>}
      </Card>
      {(report.anomalies || []).length > 0 && (
        <Card className="panel-card table-panel" variant="borderless" title="Chênh lệch ≥15% so với giá nội bộ">
          <Table size="small" pagination={false} rowKey={(r) => `${r.product_id}-${r.source_id}`} dataSource={report.anomalies}
            columns={[
              { title: 'SẢN PHẨM', dataIndex: 'product_id' },
              { title: 'NGUỒN', dataIndex: 'source_id' },
              { title: 'CHÊNH LỆCH', dataIndex: 'change_percent', render: (v) => <span className={v > 0 ? 'price-up' : 'price-down'}>{formatPercent(v)}</span> },
            ]} />
        </Card>
      )}
    </>
  );
}
