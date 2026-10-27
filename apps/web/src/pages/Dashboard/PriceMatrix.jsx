import { SearchOutlined } from '@ant-design/icons';
import { Input, Space, Switch, Table, Tag } from 'antd';
import { useMemo, useState } from 'react';
import { buildMatrix } from '../../utils/matrix.js';
import { formatDateTime } from '../../utils/timeHelpers.js';
import { PriceCell } from './PriceCell.jsx';
import { PriceHistoryModal } from './PriceHistoryModal.jsx';

export function PriceMatrix({ matrix, products, sources, referenceSource = 'internal' }) {
  const [search, setSearch] = useState('');
  const [anomalyOnly, setAnomalyOnly] = useState(false);
  const [historyProduct, setHistoryProduct] = useState(null);

  const { columns: sourceColumns, products: rows } = useMemo(
    () => buildMatrix(matrix?.data, { products, sources, referenceSource }),
    [matrix, products, sources, referenceSource],
  );
  const sourceNames = useMemo(
    () => Object.fromEntries(sourceColumns.map((s) => [s.source_id, s.name])),
    [sourceColumns],
  );

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return rows.filter((row) => (!query || row.name.toLowerCase().includes(query) || row.product_id.includes(query)) && (!anomalyOnly || row.anomaly));
  }, [rows, search, anomalyOnly]);

  const columns = [
    {
      title: 'SẢN PHẨM',
      dataIndex: 'name',
      key: 'name',
      fixed: 'left',
      width: 200,
      render: (name, row) => (
        <div className="table-primary">
          <strong>{name}</strong>
          <span>{row.product_id}{row.anomaly && <Tag color="red" className="inline-tag">Chênh ≥15%</Tag>}</span>
        </div>
      ),
    },
    ...sourceColumns.map((source) => ({
      title: source.isReference ? `${source.name} (tham chiếu)` : source.name,
      key: source.source_id,
      align: 'center',
      width: 170,
      render: (_, row) => {
        const cell = row.cells[source.source_id];
        return (
          <PriceCell
            cell={cell}
            isReference={source.isReference}
            isMin={cell != null && !cell.stale && cell.price === row.min}
            isMax={cell != null && !cell.stale && cell.price === row.max}
          />
        );
      },
    })),
  ];

  return (
    <div>
      <div className="table-toolbar">
        <div>
          <h2>Ma trận so sánh giá (24 giờ)</h2>
          <p>
            Cập nhật đến {formatDateTime(matrix?.window_end)} · Chỉ SKU đã xác nhận · Bấm vào một dòng để xem lịch sử 30 ngày
          </p>
        </div>
        <Space wrap>
          <Input allowClear prefix={<SearchOutlined />} placeholder="Tìm sản phẩm" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Tìm sản phẩm trong ma trận" />
          <span className="switch-label">Chỉ chênh lệch lớn <Switch size="small" checked={anomalyOnly} onChange={setAnomalyOnly} /></span>
        </Space>
      </div>
      <div className="matrix-legend">
        <span><i className="legend-swatch price-cell-min" /> Giá thấp nhất</span>
        <span><i className="legend-swatch price-cell-max" /> Giá cao nhất</span>
        <span><Tag color="orange">Quá hạn</Tag> dữ liệu cũ hơn 24 giờ, không dùng để so sánh</span>
      </div>
      <Table
        columns={columns}
        dataSource={filtered}
        rowKey="product_id"
        size="middle"
        scroll={{ x: 200 + sourceColumns.length * 170 }}
        pagination={{ pageSize: 20, hideOnSinglePage: true, showTotal: (total) => `${total} sản phẩm` }}
        locale={{ emptyText: 'Chưa có giá nào trong 24 giờ qua.' }}
        onRow={(row) => ({ onClick: () => setHistoryProduct(row), className: 'clickable-row' })}
      />
      <PriceHistoryModal product={historyProduct} sourceNames={sourceNames} onClose={() => setHistoryProduct(null)} />
    </div>
  );
}
