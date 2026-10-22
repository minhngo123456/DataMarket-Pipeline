import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  CloudUploadOutlined,
  DownloadOutlined,
  PlusOutlined,
  RightOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import {
  Button,
  Card,
  Col,
  Form,
  Input,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

const { Text } = Typography;

function PageHeading({ eyebrow = 'DATA WORKSPACE', title, description, actions }) {
  return (
    <div className="page-heading">
      <div>
        <span className="page-eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

function SampleLabel({ children = 'DEMO' }) {
  return <span className="sample-label">{children}</span>;
}

function StatusTag({ value }) {
  const status = {
    'Đang hoạt động': 'success',
    'Hoạt động': 'success',
    'Đã hoàn tất': 'success',
    'Đang xử lý': 'processing',
    'Cần kiểm tra': 'warning',
    'Tạm dừng': 'default',
    'Thất bại': 'error',
  }[value] || 'default';
  return <Tag color={status}>{value}</Tag>;
}

const statCards = [
  { title: 'Sản phẩm đang theo dõi', value: '1,284', suffix: 'SKU', trend: '+8.2%', rising: true, tone: 'blue' },
  { title: 'Nguồn dữ liệu', value: '06', suffix: 'nguồn', trend: '+1 tháng này', rising: true, tone: 'violet' },
  { title: 'Giá cập nhật hôm nay', value: '942', suffix: 'bản ghi', trend: '98.4% thành công', rising: true, tone: 'green' },
  { title: 'Cảnh báo giá', value: '18', suffix: 'cần xem', trend: '5 mới trong ngày', rising: false, tone: 'amber' },
];

const recentProducts = [
  { key: '1', name: 'Sữa rửa mặt dịu nhẹ', sku: 'SKU-002481', category: 'Chăm sóc da', ourPrice: '189.000 ₫', marketPrice: '179.000 ₫', change: '-5,3%', status: 'Cần kiểm tra' },
  { key: '2', name: 'Tai nghe Bluetooth Pro', sku: 'SKU-002477', category: 'Điện tử', ourPrice: '1.290.000 ₫', marketPrice: '1.350.000 ₫', change: '+4,6%', status: 'Hoạt động' },
  { key: '3', name: 'Bình giữ nhiệt 650ml', sku: 'SKU-002465', category: 'Gia dụng', ourPrice: '245.000 ₫', marketPrice: '239.000 ₫', change: '-2,4%', status: 'Hoạt động' },
];

const productColumns = [
  { title: 'SẢN PHẨM', dataIndex: 'name', key: 'name', render: (value, row) => <div className="table-primary"><strong>{value}</strong><span>{row.sku}</span></div> },
  { title: 'DANH MỤC', dataIndex: 'category', key: 'category' },
  { title: 'GIÁ CỦA BẠN', dataIndex: 'ourPrice', key: 'ourPrice' },
  { title: 'GIÁ THỊ TRƯỜNG', dataIndex: 'marketPrice', key: 'marketPrice' },
  { title: 'BIẾN ĐỘNG', dataIndex: 'change', key: 'change', render: (value) => <span className={value.startsWith('+') ? 'price-up' : 'price-down'}>{value}</span> },
  { title: 'TRẠNG THÁI', dataIndex: 'status', key: 'status', render: (value) => <StatusTag value={value} /> },
];

const sourceRows = [
  { key: 's1', name: 'Shopee Store', type: 'Sàn thương mại điện tử', account: 'shop-abc', updated: '5 phút trước', products: 684, status: 'Đang hoạt động' },
  { key: 's2', name: 'Lazada Store', type: 'Sàn thương mại điện tử', account: 'shop-abc-official', updated: '18 phút trước', products: 391, status: 'Đang hoạt động' },
  { key: 's3', name: 'Tiki Store', type: 'Sàn thương mại điện tử', account: 'abc-store', updated: 'Hôm qua, 16:42', products: 209, status: 'Cần kiểm tra' },
];

const reportRows = [
  { key: 'r1', id: 'RPT-2026-1022', name: 'Theo dõi giá tuần 43', type: 'Tổng hợp giá', created: '22/10/2026 · 09:30', products: 1284, status: 'Đã hoàn tất' },
  { key: 'r2', id: 'RPT-2026-1021', name: 'Biến động ngành chăm sóc da', type: 'Phân tích ngành hàng', created: '21/10/2026 · 16:15', products: 326, status: 'Đã hoàn tất' },
  { key: 'r3', id: 'RPT-2026-1019', name: 'Đối chiếu đối thủ tháng 10', type: 'So sánh đối thủ', created: '20/10/2026 · 11:05', products: 572, status: 'Đã hoàn tất' },
];

export function DashboardPage() {
  return (
    <>
      <PageHeading
        eyebrow="THỨ TƯ, 22 THÁNG 10, 2026"
        title="Tổng quan"
        description="Theo dõi dữ liệu thị trường và biến động giá của cửa hàng."
        actions={<Button icon={<DownloadOutlined />} onClick={() => message.info('Tải báo cáo sẽ được nối với API ở bước tiếp theo.')}>Xuất báo cáo</Button>}
      />
      <Row gutter={[16, 16]} className="stat-grid">
        {statCards.map((item) => (
          <Col xs={24} sm={12} xl={6} key={item.title}>
            <Card className={`stat-card stat-${item.tone}`} bordered={false}>
              <div className="stat-heading"><span>{item.title}</span><SampleLabel /></div>
              <Statistic value={item.value} suffix={<small>{item.suffix}</small>} />
              <div className="stat-trend">{item.rising ? <ArrowUpOutlined /> : <ArrowDownOutlined />} {item.trend}</div>
            </Card>
          </Col>
        ))}
      </Row>

      <Row gutter={[16, 16]} className="dashboard-middle">
        <Col xs={24} xl={16}>
          <Card className="panel-card" bordered={false}>
            <div className="card-heading"><div><h2>Xu hướng giá thị trường</h2><p>Giá trung bình trên các nguồn dữ liệu · 7 ngày gần nhất</p></div><Select size="small" defaultValue="7 ngày" options={['7 ngày', '30 ngày', '90 ngày'].map((label) => ({ value: label, label }))} /></div>
            <div className="chart-legend"><span><i className="legend-dot legend-blue" /> Cửa hàng của bạn</span><span><i className="legend-dot legend-orange" /> Giá trung bình thị trường</span></div>
            <div className="trend-chart" aria-label="Biểu đồ xu hướng giá mẫu">
              <div className="chart-grid"><span>1.5tr</span><span>1.2tr</span><span>900k</span><span>600k</span><span>300k</span></div>
              <div className="chart-bars">{[48, 56, 45, 67, 60, 74, 66, 83, 70, 76, 88, 79].map((height, index) => <div className="bar-group" key={index}><i className="bar-blue" style={{ height: `${height}%` }} /><i className="bar-orange" style={{ height: `${Math.max(18, height - 15 + (index % 3) * 7)}%` }} /></div>)}</div>
              <div className="chart-x-axis"><span>16/10</span><span>17/10</span><span>18/10</span><span>19/10</span><span>20/10</span><span>21/10</span><span>22/10</span></div>
            </div>
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card className="panel-card highlights-card" bordered={false}>
            <div className="card-heading"><div><h2>Điểm đáng chú ý</h2><p>Cập nhật trong hôm nay</p></div><SampleLabel /></div>
            <div className="highlight-item"><span className="highlight-icon highlight-red">↓</span><div><strong>Giá giảm mạnh</strong><p>12 sản phẩm giảm trên 10% tại Shopee</p></div><RightOutlined /></div>
            <div className="highlight-item"><span className="highlight-icon highlight-green">↑</span><div><strong>Giá đối thủ tăng</strong><p>8 sản phẩm trong ngành điện tử</p></div><RightOutlined /></div>
            <div className="highlight-item"><span className="highlight-icon highlight-blue">↻</span><div><strong>Nguồn cần đồng bộ</strong><p>Tiki Store chưa cập nhật trong 24 giờ</p></div><RightOutlined /></div>
            <Link className="text-link" to="/reports">Xem tất cả báo cáo <RightOutlined /></Link>
          </Card>
        </Col>
      </Row>

      <Card className="panel-card table-panel" bordered={false}>
        <div className="card-heading"><div><h2>Sản phẩm có biến động</h2><p>Các thay đổi giá mới được ghi nhận</p></div><Link className="text-link" to="/catalog">Mở danh mục <RightOutlined /></Link></div>
        <Table columns={productColumns} dataSource={recentProducts} pagination={false} scroll={{ x: 760 }} size="middle" />
      </Card>
    </>
  );
}

export function SourcesPage() {
  const columns = [
    { title: 'NGUỒN', dataIndex: 'name', key: 'name', render: (value, row) => <div className="table-primary"><strong>{value}</strong><span>{row.type}</span></div> },
    { title: 'TÀI KHOẢN', dataIndex: 'account', key: 'account' },
    { title: 'SẢN PHẨM', dataIndex: 'products', key: 'products', render: (value) => value.toLocaleString('vi-VN') },
    { title: 'CẬP NHẬT GẦN NHẤT', dataIndex: 'updated', key: 'updated' },
    { title: 'TRẠNG THÁI', dataIndex: 'status', key: 'status', render: (value) => <StatusTag value={value} /> },
    { title: '', key: 'action', render: () => <Button type="text" onClick={() => message.info('Chi tiết nguồn sẽ kết nối với API ở bước tiếp theo.')}>Chi tiết</Button> },
  ];
  return <>
    <PageHeading eyebrow="KẾT NỐI VÀ ĐỒNG BỘ" title="Nguồn dữ liệu" description="Quản lý các kênh đang cung cấp dữ liệu sản phẩm và giá." actions={<Button type="primary" icon={<PlusOutlined />} onClick={() => message.info('Luồng thêm nguồn sẽ kết nối với API ở bước tiếp theo.')}>Thêm nguồn</Button>} />
    <Row gutter={[16, 16]} className="source-summary">
      <Col xs={24} sm={8}><Card bordered={false} className="mini-stat"><span>Nguồn đã kết nối</span><strong>6 <small>/ 8</small></strong><Progress percent={75} showInfo={false} strokeColor="#3768e5" /></Card></Col>
      <Col xs={24} sm={8}><Card bordered={false} className="mini-stat"><span>Sản phẩm đồng bộ</span><strong>1.284</strong><small>Trên tất cả các nguồn</small></Card></Col>
      <Col xs={24} sm={8}><Card bordered={false} className="mini-stat"><span>Lần đồng bộ gần nhất</span><strong className="mini-stat-text">5 phút trước</strong><small>Đồng bộ tự động đang bật</small></Card></Col>
    </Row>
    <Card className="panel-card table-panel" bordered={false}>
      <div className="table-toolbar"><div><h2>Danh sách nguồn</h2><p>Trạng thái kết nối và dữ liệu đồng bộ</p></div><Space><Input allowClear prefix={<SearchOutlined />} placeholder="Tìm nguồn dữ liệu" /><Select defaultValue="all" options={[{ value: 'all', label: 'Tất cả trạng thái' }, { value: 'active', label: 'Đang hoạt động' }, { value: 'check', label: 'Cần kiểm tra' }]} /></Space></div>
      <Table columns={columns} dataSource={sourceRows} pagination={{ pageSize: 8, hideOnSinglePage: true }} scroll={{ x: 800 }} />
    </Card>
  </>;
}

export function UploadPage() {
  const [file, setFile] = useState(null);
  const [source, setSource] = useState('shopee');
  return <>
    <PageHeading eyebrow="NHẬP DỮ LIỆU" title="Tải dữ liệu" description="Tải bảng sản phẩm hoặc giá để đưa dữ liệu mới vào workspace." actions={<Button onClick={() => message.info('Lịch sử tải lên sẽ hiển thị khi API được kết nối.')}>Lịch sử tải lên</Button>} />
    <Row gutter={[20, 20]}>
      <Col xs={24} xl={16}>
        <Card className="panel-card upload-card" bordered={false}>
          <div className="form-section-heading"><span className="step-index">1</span><div><h2>Chọn nguồn dữ liệu</h2><p>Chọn kênh mà tệp dữ liệu này thuộc về.</p></div></div>
          <Select className="full-select" size="large" value={source} onChange={setSource} options={[{ value: 'shopee', label: 'Shopee Store' }, { value: 'lazada', label: 'Lazada Store' }, { value: 'tiki', label: 'Tiki Store' }]} />
          <div className="form-section-heading upload-step-two"><span className="step-index">2</span><div><h2>Tải tệp lên</h2><p>Hỗ trợ CSV hoặc Excel, dung lượng tối đa 20 MB.</p></div></div>
          <label className="file-dropzone">
            <input type="file" accept=".csv,.xlsx,.xls" onChange={(event) => setFile(event.target.files?.[0] || null)} />
            <span className="upload-cloud"><CloudUploadOutlined /></span>
            <strong>{file ? file.name : 'Kéo thả tệp vào đây hoặc chọn từ thiết bị'}</strong>
            <span>{file ? `${(file.size / 1024).toFixed(0)} KB · Sẵn sàng để kiểm tra` : 'CSV, XLSX hoặc XLS · Tối đa 20 MB'}</span>
            <span className="browse-button">Chọn tệp</span>
          </label>
          <div className="upload-footer"><Button type="primary" disabled={!file} onClick={() => message.info('Tệp đã chọn. Tải lên sẽ hoạt động khi API backend được triển khai.')}>Kiểm tra dữ liệu</Button><Text type="secondary">Tệp sẽ được kiểm tra trước khi nhập.</Text></div>
        </Card>
      </Col>
      <Col xs={24} xl={8}>
        <Card className="panel-card help-card" bordered={false}><h2>Định dạng tệp</h2><p>Đảm bảo tệp có các cột nhận diện sản phẩm và giá.</p><div className="format-row"><span>SKU</span><span className="format-required">Bắt buộc</span></div><div className="format-row"><span>Tên sản phẩm</span><span className="format-required">Bắt buộc</span></div><div className="format-row"><span>Giá bán</span><span className="format-required">Bắt buộc</span></div><div className="format-row"><span>URL sản phẩm</span><span>Tùy chọn</span></div><Button type="link" className="template-link" icon={<DownloadOutlined />} onClick={() => message.info('Tệp mẫu sẽ được cung cấp ở bước tích hợp tải dữ liệu.')}>Tải tệp mẫu</Button></Card>
      </Col>
    </Row>
  </>;
}

const catalogRows = [
  ...recentProducts,
  { key: '4', name: 'Kem chống nắng SPF50', sku: 'SKU-002459', category: 'Chăm sóc da', ourPrice: '329.000 ₫', marketPrice: '319.000 ₫', change: '-3,0%', status: 'Hoạt động' },
  { key: '5', name: 'Bàn phím cơ không dây', sku: 'SKU-002452', category: 'Điện tử', ourPrice: '890.000 ₫', marketPrice: '925.000 ₫', change: '+3,9%', status: 'Hoạt động' },
  { key: '6', name: 'Hộp đựng thực phẩm thủy tinh', sku: 'SKU-002441', category: 'Gia dụng', ourPrice: '159.000 ₫', marketPrice: '149.000 ₫', change: '-6,3%', status: 'Cần kiểm tra' },
];

export function CatalogPage() {
  const [query, setQuery] = useState('');
  const filteredRows = useMemo(() => catalogRows.filter((row) => `${row.name} ${row.sku} ${row.category}`.toLowerCase().includes(query.toLowerCase())), [query]);
  return <>
    <PageHeading eyebrow="SẢN PHẨM VÀ GIÁ" title="Danh mục sản phẩm" description="Tra cứu sản phẩm, theo dõi giá của bạn và so sánh với thị trường." actions={<Button type="primary" icon={<PlusOutlined />} onClick={() => message.info('Thêm sản phẩm sẽ kết nối với API ở bước tiếp theo.')}>Thêm sản phẩm</Button>} />
    <Row gutter={[16, 16]} className="catalog-summary">
      <Col xs={12} md={6}><Card className="mini-stat" bordered={false}><span>Tổng sản phẩm</span><strong>1.284</strong><small>Đang theo dõi</small></Card></Col>
      <Col xs={12} md={6}><Card className="mini-stat" bordered={false}><span>Đã khớp thị trường</span><strong>1.102</strong><small>85,8% sản phẩm</small></Card></Col>
      <Col xs={12} md={6}><Card className="mini-stat" bordered={false}><span>Cần cập nhật</span><strong className="text-amber">18</strong><small>Có biến động giá</small></Card></Col>
      <Col xs={12} md={6}><Card className="mini-stat" bordered={false}><span>Chưa khớp</span><strong>182</strong><small>Cần xác nhận sản phẩm</small></Card></Col>
    </Row>
    <Card className="panel-card table-panel" bordered={false}>
      <div className="table-toolbar"><div><h2>Tất cả sản phẩm</h2><p>Giá được tổng hợp từ các nguồn đã kết nối</p></div><Space><Input allowClear prefix={<SearchOutlined />} placeholder="Tìm tên hoặc SKU" value={query} onChange={(event) => setQuery(event.target.value)} /><Select defaultValue="all" options={[{ value: 'all', label: 'Tất cả danh mục' }, { value: 'beauty', label: 'Chăm sóc da' }, { value: 'electronics', label: 'Điện tử' }]} /></Space></div>
      <Table columns={productColumns} dataSource={filteredRows} pagination={{ pageSize: 6, showSizeChanger: false }} scroll={{ x: 760 }} />
    </Card>
  </>;
}

export function ReportsPage() {
  const navigate = useNavigate();
  const columns = [
    { title: 'BÁO CÁO', dataIndex: 'name', key: 'name', render: (value, row) => <div className="table-primary"><strong>{value}</strong><span>{row.id} · {row.type}</span></div> },
    { title: 'NGÀY TẠO', dataIndex: 'created', key: 'created' },
    { title: 'SẢN PHẨM', dataIndex: 'products', key: 'products', render: (value) => value.toLocaleString('vi-VN') },
    { title: 'TRẠNG THÁI', dataIndex: 'status', key: 'status', render: (value) => <StatusTag value={value} /> },
    { title: '', key: 'action', render: (_, row) => <Button type="text" onClick={() => navigate(`/reports/${row.id}`)}>Xem báo cáo <RightOutlined /></Button> },
  ];
  return <>
    <PageHeading eyebrow="PHÂN TÍCH THỊ TRƯỜNG" title="Báo cáo" description="Xem lại báo cáo giá và các phân tích đã tạo." actions={<Button type="primary" icon={<PlusOutlined />} onClick={() => message.info('Tạo báo cáo sẽ được nối với API ở bước tiếp theo.')}>Tạo báo cáo</Button>} />
    <Row gutter={[16, 16]} className="report-summary"><Col xs={24} sm={8}><Card className="mini-stat" bordered={false}><span>Báo cáo đã tạo</span><strong>24</strong><small>Trong 30 ngày qua</small></Card></Col><Col xs={24} sm={8}><Card className="mini-stat" bordered={false}><span>Đang xử lý</span><strong>02</strong><small>Thời gian trung bình 3 phút</small></Card></Col><Col xs={24} sm={8}><Card className="mini-stat" bordered={false}><span>Lần tạo gần nhất</span><strong className="mini-stat-text">Hôm nay, 09:30</strong><small>Theo dõi giá tuần 43</small></Card></Col></Row>
    <Card className="panel-card table-panel" bordered={false}><div className="table-toolbar"><div><h2>Lịch sử báo cáo</h2><p>Các báo cáo mới nhất trong workspace</p></div><Space><Input prefix={<SearchOutlined />} placeholder="Tìm báo cáo" /><Select defaultValue="30" options={[{ value: '30', label: '30 ngày qua' }, { value: '90', label: '90 ngày qua' }, { value: 'all', label: 'Tất cả thời gian' }]} /></Space></div><Table columns={columns} dataSource={reportRows} pagination={{ pageSize: 8, hideOnSinglePage: true }} scroll={{ x: 740 }} /></Card>
  </>;
}

export function ReportDetailPage() {
  const { id } = useParams();
  const report = reportRows.find((item) => item.id === id) || reportRows[0];
  return <>
    <div className="back-link"><Link to="/reports">← Quay lại báo cáo</Link></div>
    <PageHeading eyebrow={report.id} title={report.name} description={`${report.type} · Tạo lúc ${report.created}`} actions={<Button icon={<DownloadOutlined />} onClick={() => message.info('Xuất tệp sẽ được kết nối với API báo cáo.')}>Xuất tệp</Button>} />
    <Row gutter={[16, 16]}><Col xs={24} md={8}><Card className="mini-stat" bordered={false}><span>Sản phẩm trong báo cáo</span><strong>{report.products.toLocaleString('vi-VN')}</strong><small>Được tổng hợp từ 6 nguồn</small></Card></Col><Col xs={24} md={8}><Card className="mini-stat" bordered={false}><span>Thay đổi giá trung bình</span><strong className="price-down">-2,4%</strong><small>So với kỳ trước</small></Card></Col><Col xs={24} md={8}><Card className="mini-stat" bordered={false}><span>Trạng thái</span><strong className="mini-stat-text"><StatusTag value={report.status} /></strong><small>Dữ liệu xem trước</small></Card></Col></Row>
    <Card className="panel-card table-panel" bordered={false}><div className="card-heading"><div><h2>Sản phẩm có thay đổi lớn</h2><p>Danh sách minh họa trong báo cáo này</p></div><SampleLabel /></div><Table columns={productColumns} dataSource={recentProducts} pagination={false} scroll={{ x: 760 }} /></Card>
  </>;
}

const memberRows = [
  { key: 'm1', name: 'Nguyễn Minh', email: 'minh@example.com', role: 'admin', lastActive: 'Đang hoạt động' },
  { key: 'm2', name: 'Lê An', email: 'an@example.com', role: 'analyst', lastActive: 'Hôm nay, 09:18' },
  { key: 'm3', name: 'Trần Bình', email: 'binh@example.com', role: 'viewer', lastActive: 'Hôm qua, 16:42' },
];

export function MembersPage() {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const columns = [
    { title: 'THÀNH VIÊN', dataIndex: 'name', key: 'name', render: (value, row) => <div className="table-primary"><strong>{value}</strong><span>{row.email}</span></div> },
    { title: 'VAI TRÒ', dataIndex: 'role', key: 'role', render: (value) => <Tag color={value === 'admin' ? 'blue' : value === 'analyst' ? 'purple' : 'default'}>{value}</Tag> },
    { title: 'HOẠT ĐỘNG GẦN NHẤT', dataIndex: 'lastActive', key: 'lastActive' },
    { title: '', key: 'action', render: () => <Button type="text" onClick={() => message.info('Chỉnh sửa thành viên sẽ được kết nối với API.')}>Quản lý</Button> },
  ];
  return <>
    <PageHeading eyebrow="QUẢN TRỊ WORKSPACE" title="Thành viên" description="Quản lý người dùng và quyền truy cập của workspace." actions={<Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>Mời thành viên</Button>} />
    <Row gutter={[16, 16]}><Col xs={24} sm={8}><Card className="mini-stat" bordered={false}><span>Thành viên</span><strong>3 <small>/ 5</small></strong><Progress percent={60} showInfo={false} strokeColor="#3768e5" /></Card></Col><Col xs={24} sm={8}><Card className="mini-stat" bordered={false}><span>Quản trị viên</span><strong>1</strong><small>Toàn quyền workspace</small></Card></Col><Col xs={24} sm={8}><Card className="mini-stat" bordered={false}><span>Lời mời đang chờ</span><strong>0</strong><small>Chưa có lời mời nào</small></Card></Col></Row>
    <Card className="panel-card table-panel" bordered={false}><div className="table-toolbar"><div><h2>Danh sách thành viên</h2><p>Quyền hạn được áp dụng theo vai trò</p></div><Select defaultValue="all" options={[{ value: 'all', label: 'Tất cả vai trò' }, { value: 'admin', label: 'Admin' }, { value: 'analyst', label: 'Analyst' }, { value: 'viewer', label: 'Viewer' }]} /></div><Table columns={columns} dataSource={memberRows} pagination={false} scroll={{ x: 680 }} /></Card>
    <Modal title="Mời thành viên" open={open} okText="Gửi lời mời" cancelText="Hủy" onCancel={() => setOpen(false)} onOk={() => form.validateFields().then(() => { setOpen(false); form.resetFields(); message.info('Biểu mẫu đã sẵn sàng. Gửi lời mời sẽ hoạt động khi backend được kết nối.'); })}>
      <p className="modal-description">Thêm thành viên vào workspace và chọn quyền truy cập.</p>
      <Form form={form} layout="vertical"><Form.Item label="Email" name="email" rules={[{ required: true, type: 'email', message: 'Nhập email hợp lệ.' }]}><Input placeholder="ten@congty.com" /></Form.Item><Form.Item label="Vai trò" name="role" rules={[{ required: true, message: 'Chọn vai trò.' }]}><Select placeholder="Chọn vai trò" options={[{ value: 'analyst', label: 'Analyst · tải và phân tích dữ liệu' }, { value: 'viewer', label: 'Viewer · chỉ xem dữ liệu' }]} /></Form.Item></Form>
    </Modal>
  </>;
}
