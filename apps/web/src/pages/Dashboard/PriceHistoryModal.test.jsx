import { render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { marketDataApi } from '../../services/marketDataApi.js';
import { PriceHistoryModal } from './PriceHistoryModal.jsx';

vi.mock('../../services/marketDataApi.js', () => ({ marketDataApi: { getPriceHistory: vi.fn() } }));
vi.mock('./PriceHistoryChart.jsx', () => ({ PriceHistoryChart: ({ points }) => <pre data-testid="history-points">{JSON.stringify(points)}</pre> }));

beforeEach(() => {
  marketDataApi.getPriceHistory.mockReset().mockResolvedValue({ data: [
    { source_id: 'shop', price: 85000, price_basis: 'tax_excluded', observed_at: '2026-11-14T01:00:00Z' },
    { source_id: 'shop', price: 90000, price_basis: 'tax_included', observed_at: '2026-11-14T02:00:00Z' },
    { source_id: 'shop', price: 99999, price_basis: 'unknown', observed_at: '2026-11-14T03:00:00Z' },
  ] });
});

it('keeps history on the selected tax basis, including after switching the basis', async () => {
  const props = { product: { product_id: 'paper', name: 'Paper' }, sourceNames: { shop: 'Shop' }, onClose: vi.fn() };
  const { rerender } = render(<PriceHistoryModal {...props} priceBasis="tax_excluded" />);
  expect(JSON.parse((await screen.findByTestId('history-points')).textContent)).toEqual([
    { date: '2026-11-14', Shop: 85000 },
  ]);
  rerender(<PriceHistoryModal {...props} priceBasis="tax_included" />);
  expect(JSON.parse(screen.getByTestId('history-points').textContent)).toEqual([
    { date: '2026-11-14', Shop: 90000 },
  ]);
});
