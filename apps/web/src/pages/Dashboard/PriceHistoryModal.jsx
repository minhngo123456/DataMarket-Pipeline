import { Modal, Spin } from 'antd';
import { useMemo } from 'react';
import { apiErrorMessage, useApiData } from '../../hooks/useApiData.js';
import { mockPriceHistory } from '../../mocks/data.js';
import { marketDataApi } from '../../services/marketDataApi.js';
import { buildHistorySeries } from '../../utils/matrix.js';
import { LoadError, SampleLabel } from '../../components/PageHeading.jsx';
import { PriceHistoryChart } from './PriceHistoryChart.jsx';

function HistoryBody({ product, sourceNames, priceBasis }) {
  const history = useApiData(
    () => marketDataApi.getPriceHistory(product.product_id, { days: 30 }),
    () => mockPriceHistory(product.product_id),
    [product.product_id],
  );
  const chart = useMemo(() => buildHistorySeries(
    (history.data?.data || []).filter((row) => row.price_basis === priceBasis), sourceNames,
  ), [history.data, sourceNames, priceBasis]);

  if (history.loading) return <div className="center-block"><Spin /></div>;
  if (history.error) return <LoadError error={apiErrorMessage(history.error)} onRetry={history.reload} />;
  return (
    <>
      {history.isMock && <SampleLabel />}
      <PriceHistoryChart series={chart.series} points={chart.points} />
    </>
  );
}

export function PriceHistoryModal({ product, sourceNames, onClose, priceBasis = 'tax_included' }) {
  return (
    <Modal open={Boolean(product)} onCancel={onClose} footer={null} width={820} destroyOnHidden title={`Lịch sử giá 30 ngày: ${product?.name || ''} (${priceBasis === 'tax_excluded' ? 'chưa gồm thuế' : 'đã gồm thuế'})`}>
      {product && <HistoryBody product={product} sourceNames={sourceNames} priceBasis={priceBasis} />}
    </Modal>
  );
}
