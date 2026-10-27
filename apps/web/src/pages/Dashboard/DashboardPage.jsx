import { ReloadOutlined, RightOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Col, Row, Spin, Statistic, Tag } from 'antd';
import { Link } from 'react-router-dom';
import { LoadError, PageHeading, SampleLabel } from '../../components/PageHeading.jsx';
import { apiErrorMessage, useApiData } from '../../hooks/useApiData.js';
import { mockAlerts, mockMatrix, mockProducts, mockSources, mockStats } from '../../mocks/data.js';
import { marketDataApi } from '../../services/marketDataApi.js';
import { formatDateTime, isStale, timeAgo } from '../../utils/timeHelpers.js';
import { PriceMatrix } from './PriceMatrix.jsx';

const KIND_LABEL = { csv: 'CSV', api: 'API', web: 'WEB' };
const ALERT_TYPE = { error: 'error', warning: 'warning', info: 'info' };

function SourceStatus({ source }) {
  if (!source.enabled) return <Tag>Đã tắt</Tag>;
  if (isStale(source.updated_at)) return <Tag color="orange">Chưa cập nhật &gt;24h</Tag>;
  return <Tag color="green">Hoạt động</Tag>;
}

export function DashboardPage() {
  const stats = useApiData(() => marketDataApi.getDashboardStats(), mockStats);
  const sources = useApiData(() => marketDataApi.getSources(), mockSources);
  const alerts = useApiData(() => marketDataApi.getAlerts({ limit: 20 }), mockAlerts);
  const matrix = useApiData(() => marketDataApi.getPriceMatrix({ reference_source: 'internal', basis: 'tax_included' }), mockMatrix);
  const products = useApiData(() => marketDataApi.getProducts({ match_status: 'confirmed', limit: 100 }), mockProducts);

  const isMock = [stats, sources, alerts, matrix, products].some((r) => r.isMock);
  const s = stats.data;
  const report = s?.latest_report;
  const reloadAll = () => [stats, sources, alerts, matrix, products].forEach((r) => r.reload());

  return (
    <>
      <PageHeading
        eyebrow="TỔNG QUAN"
        title="Tổng quan"
        description="Giá thị trường 24 giờ qua, trạng thái nguồn và cảnh báo cần xử lý."
        actions={<Button icon={<ReloadOutlined />} onClick={reloadAll}>Tải lại</Button>}
      />
      {isMock && <Alert className="page-alert" type="warning" showIcon title="Chưa kết nối được API, trang đang hiển thị dữ liệu mẫu." />}

      {stats.error ? <LoadError error={apiErrorMessage(stats.error)} onRetry={stats.reload} /> : (
        <Row gutter={[16, 16]} className="stat-grid">
          <Col xs={24} sm={12} xl={6}>
            <Card className="stat-card stat-blue" variant="borderless" loading={stats.loading}>
              <div className="stat-heading"><span>SKU đang theo dõi</span>{stats.isMock && <SampleLabel />}</div>
              <Statistic value={s?.total_skus ?? 0} suffix={<small>/ {s?.max_skus ?? 100}</small>} />
            </Card>
          </Col>
          <Col xs={24} sm={12} xl={6}>
            <Card className="stat-card stat-violet" variant="borderless" loading={stats.loading}>
              <div className="stat-heading"><span>Nguồn hoạt động</span></div>
              <Statistic value={s?.active_sources ?? 0} suffix={<small>/ {s?.total_sources ?? 0} nguồn</small>} />
            </Card>
          </Col>
          <Col xs={24} sm={12} xl={6}>
            <Card className="stat-card stat-amber" variant="borderless" loading={stats.loading}>
              <div className="stat-heading"><span>SKU chờ duyệt</span></div>
              <Statistic value={s?.pending_skus ?? 0} />
              <Link className="text-link" to="/catalog">Mở Catalog <RightOutlined /></Link>
            </Card>
          </Col>
          <Col xs={24} sm={12} xl={6}>
            <Card className="stat-card stat-green" variant="borderless" loading={stats.loading}>
              <div className="stat-heading"><span>Báo cáo mới nhất</span></div>
              <Statistic value={report?.date || 'Chưa có'} styles={{ content: { fontSize: 22 } }} />
              {report && <Link className="text-link" to={`/reports/${report.report_id}`}>Xem báo cáo <RightOutlined /></Link>}
            </Card>
          </Col>
        </Row>
      )}

      <Card className="panel-card table-panel" variant="borderless">
        {matrix.loading || products.loading || sources.loading ? <div className="center-block"><Spin /></div>
          : matrix.error ? <LoadError error={apiErrorMessage(matrix.error)} onRetry={matrix.reload} />
            : <PriceMatrix matrix={matrix.data} products={products.data?.data || []} sources={sources.data?.data || []} />}
      </Card>

      <Row gutter={[16, 16]} className="dashboard-middle">
        <Col xs={24} xl={12}>
          <Card className="panel-card" variant="borderless" title="Trạng thái nguồn" loading={sources.loading}>
            {sources.error ? <LoadError error={apiErrorMessage(sources.error)} onRetry={sources.reload} /> : (
              (sources.data?.data || []).length === 0 ? <p className="empty-note">Chưa cấu hình nguồn nào.</p> : (
                <ul className="source-status-list">
                  {sources.data.data.map((source) => (
                    <li key={source.source_id}>
                      <div>
                        <strong>{source.name}</strong> <Tag>{KIND_LABEL[source.kind] || source.kind}</Tag>
                        <div><SourceStatus source={source} /></div>
                      </div>
                      <span className="muted" title={formatDateTime(source.updated_at)}>{timeAgo(source.updated_at)}</span>
                    </li>
                  ))}
                </ul>
              )
            )}
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card className="panel-card" variant="borderless" title="Cảnh báo" loading={alerts.loading}>
            {alerts.error ? <LoadError error={apiErrorMessage(alerts.error)} onRetry={alerts.reload} />
              : (alerts.data?.data || []).length === 0 ? <p className="empty-note">Không có cảnh báo.</p>
                : alerts.data.data.map((alert) => (
                  <Alert
                    key={alert.id}
                    className="alert-item"
                    type={ALERT_TYPE[alert.severity] || 'info'}
                    showIcon
                    title={alert.title}
                    description={<>{alert.message} <span className="muted">· {timeAgo(alert.created_at)}</span></>}
                  />
                ))}
          </Card>
        </Col>
      </Row>
    </>
  );
}
