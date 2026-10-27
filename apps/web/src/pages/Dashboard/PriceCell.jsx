import { Tag, Tooltip } from 'antd';
import { formatPercent, formatVND } from '../../utils/priceFormat.js';
import { formatDateTime, timeAgo } from '../../utils/timeHelpers.js';

export function PriceCell({ cell, isMin = false, isMax = false, isReference = false }) {
  if (!cell) {
    return <div className="price-cell price-cell-empty" aria-label="Chưa có dữ liệu trong 24 giờ">—</div>;
  }

  const tone = isMin ? 'price-cell-min' : isMax ? 'price-cell-max' : '';
  return (
    <Tooltip
      title={(
        <div>
          <div>Nguồn: {cell.source_name}</div>
          <div>Thời gian: {formatDateTime(cell.observed_at)}</div>
          <div>Giá: {formatVND(cell.price)}</div>
          {!isReference && cell.change_percent != null && <div>So với giá nội bộ: {formatPercent(cell.change_percent)}</div>}
        </div>
      )}
    >
      <div className={`price-cell ${tone}`} data-testid="price-cell">
        <strong>{formatVND(cell.price)}</strong>
        {!isReference && cell.change_percent != null && (
          <span className={cell.change_percent > 0 ? 'price-up' : cell.change_percent < 0 ? 'price-down' : ''}>
            {formatPercent(cell.change_percent)}
          </span>
        )}
        <div>
          {cell.stale
            ? <Tag color="orange">Quá hạn · {timeAgo(cell.observed_at)}</Tag>
            : <Tag color="green">{timeAgo(cell.observed_at)}</Tag>}
        </div>
        {isMin && <Tag color="blue">GIÁ THẤP</Tag>}
        {isMax && <Tag color="volcano">GIÁ CAO</Tag>}
      </div>
    </Tooltip>
  );
}
