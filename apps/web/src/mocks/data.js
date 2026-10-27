// Development fixtures. Shapes follow docs/api-contract-frontend.md and the
// handlers in services/api so pages can switch to the real API unchanged.
const hoursAgo = (hours) => new Date(Date.now() - hours * 3600 * 1000).toISOString();
const daysAgo = (days, hour = 2) => {
  const date = new Date(Date.now() - days * 86400 * 1000);
  date.setUTCHours(hour, 0, 0, 0);
  return date.toISOString();
};

export const mockSources = {
  data: [
    { source_id: 'internal', name: 'Bảng giá nội bộ (CSV)', kind: 'csv', enabled: true, updated_at: hoursAgo(2) },
    { source_id: 'tiki-api', name: 'Tiki API', kind: 'api', enabled: true, updated_at: hoursAgo(1) },
    { source_id: 'sendo-web', name: 'Sendo Web', kind: 'web', enabled: false, updated_at: hoursAgo(30) },
  ],
  total: 3,
  page: 1,
  limit: 20,
};

export const mockStats = {
  total_skus: 3,
  max_skus: 100,
  total_sources: 3,
  active_sources: 2,
  pending_skus: 2,
  error_count_24h: 0,
  latest_report: {
    report_id: 'rep-sample',
    date: daysAgo(0).slice(0, 10),
    status: 'ready',
    ai_status: 'ai_generated',
    summary_preview: 'Tiki giảm giá áo thun 4%, quần jean chênh 15.000 ₫ giữa các nguồn.',
  },
};

export const mockAlerts = {
  data: [
    {
      id: 'alert-anomaly-jean-sendo-web',
      type: 'price_anomaly',
      severity: 'warning',
      title: 'Chênh lệch giá bất thường ở Quần jean slim fit',
      message: "Nguồn 'sendo-web' chênh lệch +16% so với giá nội bộ.",
      created_at: hoursAgo(3),
    },
    {
      id: 'alert-pending-obs-1',
      type: 'pending_sku',
      severity: 'info',
      title: 'SKU mới cần xác nhận: AT-X01',
      message: "SKU 'AT-X01' từ nguồn 'tiki-api' chưa được map trong catalog.",
      created_at: hoursAgo(1),
    },
  ],
  total: 2,
  page: 1,
  limit: 20,
};

export const mockMatrix = {
  data: [
    { product_id: 'ao-thun', source_id: 'internal', price: 125000, observed_at: hoursAgo(2), change_percent: 0 },
    { product_id: 'ao-thun', source_id: 'tiki-api', price: 120000, observed_at: hoursAgo(1), change_percent: -4 },
    { product_id: 'quan-jean', source_id: 'internal', price: 250000, observed_at: hoursAgo(5), change_percent: 0 },
    { product_id: 'quan-jean', source_id: 'tiki-api', price: 245000, observed_at: hoursAgo(1), change_percent: -2 },
    { product_id: 'quan-jean', source_id: 'sendo-web', price: '290000', observed_at: hoursAgo(20), change_percent: '16.00' },
    { product_id: 'giay-sneaker', source_id: 'internal', price: 890000, observed_at: hoursAgo(3), change_percent: 0 },
    { product_id: 'giay-sneaker', source_id: 'tiki-api', price: 895000, observed_at: hoursAgo(2), change_percent: '0.56' },
  ],
  total: 7,
  page: 1,
  limit: 20,
  window_start: hoursAgo(24),
  window_end: hoursAgo(0),
  price_basis: 'tax_included',
};

export const mockProducts = {
  data: [
    { product_id: 'ao-thun', name: 'Áo thun nam basic', attributes: { size: 'M' }, match_status: 'confirmed' },
    { product_id: 'quan-jean', name: 'Quần jean slim fit', attributes: {}, match_status: 'confirmed' },
    { product_id: 'giay-sneaker', name: 'Giày sneaker trắng', attributes: {}, match_status: 'confirmed' },
    { observation_id: 'obs-1', product_id: null, source_sku: 'AT-X01', price: 119000, currency: 'VND', match_status: 'pending', source_id: 'tiki-api', reason: 'unmapped_sku' },
    { observation_id: 'obs-2', product_id: null, source_sku: 'JEAN-99', price: 260000, currency: 'VND', match_status: 'pending', source_id: 'sendo-web', reason: 'unmapped_sku' },
    { observation_id: 'obs-3', product_id: null, source_sku: 'MU-LUOI-TRAI', price: 99000, currency: 'VND', match_status: 'rejected', source_id: 'tiki-api', reason: 'unmapped_sku' },
  ],
  total: 6,
  page: 1,
  limit: 20,
};

export function mockPriceHistory(productId) {
  const base = { 'ao-thun': 125000, 'quan-jean': 250000, 'giay-sneaker': 890000 }[productId] || 100000;
  const data = [];
  for (let day = 29; day >= 0; day -= 3) {
    data.push({ observation_id: `int-${day}`, source_id: 'internal', price: base + (day % 4) * 1000, currency: 'VND', price_basis: 'tax_included', observed_at: daysAgo(day) });
    data.push({ observation_id: `tiki-${day}`, source_id: 'tiki-api', price: base - 5000 + day * 300, currency: 'VND', price_basis: 'tax_included', observed_at: daysAgo(day, 5) });
  }
  return { product_id: productId, days: 30, data, total: data.length };
}

export const mockReports = {
  data: [
    { report_id: 'rep-sample', period: daysAgo(0).slice(0, 10), status: 'ready', ai_status: 'ai_generated', summary: 'Tiki giảm giá áo thun 4%...', created_at: daysAgo(0, 1) },
    { report_id: 'rep-fallback', period: daysAgo(1).slice(0, 10), status: 'partial', ai_status: 'fallback_quantitative', summary: null, created_at: daysAgo(1, 1) },
  ],
  total: 2,
  page: 1,
  limit: 20,
};

export const mockReportDetails = {
  'rep-sample': {
    ...mockReports.data[0],
    summary:
      '### Báo cáo thị trường hằng ngày\n\n**Tóm tắt:** Trong 24 giờ qua hệ thống theo dõi 3 sản phẩm trên 2 nguồn hoạt động.\n\n- Áo thun nam basic: Tiki thấp hơn giá nội bộ 4%.\n- Quần jean slim fit: Sendo cao hơn 16%, cần kiểm tra.',
    metrics: { total_products: 3, total_observations: 7, anomaly_count: 1 },
    anomalies: [{ product_id: 'quan-jean', source_id: 'sendo-web', change_percent: 16 }],
    download_url: null,
  },
  'rep-fallback': {
    ...mockReports.data[1],
    metrics: { total_products: 3, total_observations: 6, anomaly_count: 0 },
    anomalies: [],
    download_url: null,
  },
};

export const mockMembers = {
  data: [
    { user_id: 'u-admin', email: 'admin@example.com', role: 'admin', status: 'active' },
    { user_id: 'u-analyst', email: 'analyst@example.com', role: 'analyst', status: 'active' },
    { user_id: 'u-viewer', email: 'viewer@example.com', role: 'viewer', status: 'disabled' },
  ],
  total: 3,
  max_members: 5,
};
