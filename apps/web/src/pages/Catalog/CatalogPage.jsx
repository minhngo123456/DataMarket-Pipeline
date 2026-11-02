import { CheckOutlined, CloseOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons';
import { Alert, Badge, Button, Card, Input, Modal, Select, Space, Table, Tabs, Tag, message } from 'antd';
import { useMemo, useState } from 'react';
import { LoadError, PageHeading } from '../../components/PageHeading.jsx';
import { apiErrorMessage, useApiData } from '../../hooks/useApiData.js';
import { mockProducts, mockSources } from '../../mocks/data.js';
import { marketDataApi } from '../../services/marketDataApi.js';
import { formatVND } from '../../utils/priceFormat.js';

const STATUS_TAG = {
  confirmed: <Tag color="green">Đã xác nhận</Tag>,
  pending: <Tag color="orange">Chờ duyệt</Tag>,
  rejected: <Tag color="red">Đã từ chối</Tag>,
};

/** Suggests a catalog id from a source SKU: lowercase, only a-z 0-9 _ -. */
export function productIdFromSku(sku) {
  return String(sku || '').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 100);
}

export function rowKey(row) {
  return row.observation_id || `product-${row.product_id}`;
}

export function CatalogPage() {
  const products = useApiData(() => marketDataApi.getProducts({ match_status: 'all', limit: 100 }), mockProducts);
  const sources = useApiData(() => marketDataApi.getSources(), mockSources);
  const [tab, setTab] = useState('pending');
  const [search, setSearch] = useState('');
  const [approving, setApproving] = useState(null);
  const [target, setTarget] = useState(null);
  const [newProduct, setNewProduct] = useState(null);
  const [busy, setBusy] = useState(null);

  const rows = products.data?.data || [];
  const sourceNames = useMemo(
    () => Object.fromEntries((sources.data?.data || []).map((s) => [s.source_id, s.name || s.source_id])),
    [sources.data],
  );
  // A SKU approved on this page also becomes 'confirmed' but is a price row, not a product.
  const catalog = rows.filter((row) => row.match_status === 'confirmed' && !row.observation_id);
  const pendingCount = rows.filter((row) => row.match_status === 'pending').length;

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return rows.filter((row) => (tab === 'all' || row.match_status === tab)
      && (!query || [row.name, row.product_id, row.source_sku].some((v) => v && String(v).toLowerCase().includes(query))));
  }, [rows, tab, search]);

  const settle = (row, status, productId = null) => {
    products.setData((data) => ({
      ...data,
      data: data.data.map((item) => (rowKey(item) === rowKey(row) ? { ...item, match_status: status, product_id: productId } : item)),
    }));
  };

  const review = async (row, action, productId = null) => {
    setBusy(rowKey(row));
    try {
      if (products.isMock) {
        await new Promise((resolve) => setTimeout(resolve, 300));
      } else {
        // The path id is the target product; a reject has none, so the observation id is used.
        await marketDataApi.matchProduct(productId || row.observation_id, {
          observation_id: row.observation_id,
          product_id: productId,
          action,
        });
      }
      settle(row, action === 'approve' ? 'confirmed' : 'rejected', productId);
      message.success(action === 'approve' ? `Đã ghép ${row.source_sku} vào ${productId}.` : `Đã từ chối ${row.source_sku}.`);
      return true;
    } catch (error) {
      message.error(apiErrorMessage(error, 'Không cập nhật được SKU.'));
      if (error?.response?.status === 409) products.reload();
      return false;
    } finally {
      setBusy(null);
    }
  };

  const openApprove = (row) => {
    setApproving(row);
    setTarget(null);
    // An empty catalog has nothing to pick, so start with the create form.
    setNewProduct(catalog.length ? null : { product_id: productIdFromSku(row.source_sku), name: row.source_sku });
  };

  const approve = async () => {
    let productId = target;
    if (newProduct) {
      productId = newProduct.product_id.trim();
      const name = newProduct.name.trim();
      setBusy(rowKey(approving));
      try {
        if (!products.isMock) await marketDataApi.createProduct({ product_id: productId, name });
      } catch (error) {
        message.error(apiErrorMessage(error, 'Không tạo được sản phẩm.'));
        setBusy(null);
        return;
      }
      products.setData((data) => ({ ...data, data: [{ product_id: productId, name, match_status: 'confirmed' }, ...data.data] }));
    }
    if (await review(approving, 'approve', productId)) setApproving(null);
  };

  const newProductValid = newProduct && /^[A-Za-z0-9_-]{1,100}$/.test(newProduct.product_id.trim()) && newProduct.name.trim();

  const confirmReject = (row) => Modal.confirm({
    title: `Từ chối SKU ${row.source_sku}?`,
    content: 'Dòng giá này sẽ không vào ma trận so sánh. Lần nhập sau SKU này vẫn sẽ vào hàng chờ.',
    okText: 'Từ chối',
    okButtonProps: { danger: true },
    cancelText: 'Hủy',
    onOk: () => review(row, 'reject'),
  });

  const columns = [
    {
      title: 'SẢN PHẨM / SKU NGUỒN',
      key: 'name',
      render: (_, row) => (row.match_status === 'confirmed' && !row.observation_id
        ? <div className="table-primary"><strong>{row.name}</strong><span>{row.product_id}</span></div>
        : <div className="table-primary"><strong>{row.source_sku}</strong><span>{row.product_id ? `Đã ghép vào ${row.product_id}` : 'Chưa có trong catalog'}</span></div>),
    },
    { title: 'NGUỒN', key: 'source', render: (_, row) => (row.source_id ? sourceNames[row.source_id] || row.source_id : '—') },
    { title: 'GIÁ QUAN SÁT', key: 'price', render: (_, row) => formatVND(row.price) },
    { title: 'TRẠNG THÁI', dataIndex: 'match_status', key: 'status', render: (status) => STATUS_TAG[status] || status },
    {
      title: 'HÀNH ĐỘNG',
      key: 'actions',
      render: (_, row) => (row.match_status === 'pending' ? (
        <Space>
          <Button type="primary" size="small" icon={<CheckOutlined />} loading={busy === rowKey(row)} onClick={() => openApprove(row)}>Duyệt</Button>
          <Button danger size="small" icon={<CloseOutlined />} disabled={busy === rowKey(row)} onClick={() => confirmReject(row)}>Từ chối</Button>
        </Space>
      ) : <span className="muted">—</span>),
    },
  ];

  return (
    <>
      <PageHeading eyebrow="SẢN PHẨM VÀ GHÉP SKU" title="Danh mục sản phẩm" description="Duyệt các SKU mới từ nguồn trước khi chúng vào ma trận so sánh giá." />
      {products.isMock && <Alert className="page-alert" type="warning" showIcon title="Chưa kết nối được API, trang đang hiển thị dữ liệu mẫu. Thao tác duyệt chỉ thay đổi trên màn hình." />}
      <Card className="panel-card table-panel" variant="borderless">
        <div className="table-toolbar">
          <Tabs
            activeKey={tab}
            onChange={setTab}
            items={[
              { key: 'pending', label: <Badge count={pendingCount} size="small" offset={[10, 0]}>Chờ duyệt</Badge> },
              { key: 'confirmed', label: 'Đã xác nhận' },
              { key: 'rejected', label: 'Đã từ chối' },
              { key: 'all', label: 'Tất cả' },
            ]}
          />
          <Input allowClear prefix={<SearchOutlined />} placeholder="Tìm tên, mã sản phẩm hoặc SKU" value={search} onChange={(e) => setSearch(e.target.value)} className="toolbar-search" />
        </div>
        {products.error ? <LoadError error={apiErrorMessage(products.error)} onRetry={products.reload} /> : (
          <Table columns={columns} dataSource={filtered} rowKey={rowKey} loading={products.loading} pagination={{ pageSize: 20, hideOnSinglePage: true }} scroll={{ x: 760 }} locale={{ emptyText: tab === 'pending' ? 'Không có SKU nào chờ duyệt.' : 'Không có dữ liệu.' }} />
        )}
      </Card>

      <Modal
        open={Boolean(approving)}
        title={`Duyệt SKU ${approving?.source_sku || ''}`}
        okText="Ghép vào sản phẩm"
        cancelText="Hủy"
        okButtonProps={{ disabled: newProduct ? !newProductValid : !target, loading: busy === (approving && rowKey(approving)) }}
        onCancel={() => setApproving(null)}
        onOk={approve}
        destroyOnHidden
      >
        <p className="modal-description">Chọn sản phẩm trong catalog tương ứng, hoặc tạo sản phẩm mới. Mapping được ghi nhận cho các lần nhập sau.</p>
        {newProduct ? (
          <Space direction="vertical" className="full-select">
            <Input aria-label="Tên sản phẩm" placeholder="Tên sản phẩm, ví dụ iPhone 15 128GB" value={newProduct.name} onChange={(e) => setNewProduct({ ...newProduct, name: e.target.value })} />
            <Input aria-label="Mã sản phẩm" addonBefore="Mã" placeholder="iphone-15-128" value={newProduct.product_id} onChange={(e) => setNewProduct({ ...newProduct, product_id: e.target.value })} />
            {catalog.length > 0 && <Button type="link" onClick={() => setNewProduct(null)}>Chọn sản phẩm có sẵn</Button>}
          </Space>
        ) : (
        <>
        <Select
          className="full-select"
          showSearch
          placeholder="Chọn sản phẩm"
          value={target}
          onChange={setTarget}
          optionFilterProp="label"
          options={catalog.map((p) => ({ value: p.product_id, label: p.name ? `${p.name} (${p.product_id})` : p.product_id }))}
          notFoundContent="Catalog chưa có sản phẩm"
        />
        <Button type="link" icon={<PlusOutlined />} onClick={() => setNewProduct({ product_id: productIdFromSku(approving?.source_sku), name: approving?.source_sku || '' })}>Tạo sản phẩm mới</Button>
        </>
        )}
      </Modal>
    </>
  );
}
