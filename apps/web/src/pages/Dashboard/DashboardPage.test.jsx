import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { marketDataApi } from '../../services/marketDataApi.js';
import { DashboardPage } from './DashboardPage.jsx';

vi.mock('../../services/marketDataApi.js', () => ({
  marketDataApi: {
    getDashboardStats: vi.fn(), getSources: vi.fn(), getAlerts: vi.fn(),
    getPriceMatrix: vi.fn(), getProducts: vi.fn(),
  },
}));
vi.mock('./PriceMatrix.jsx', () => ({ PriceMatrix: ({ matrix }) => <div>{matrix?.price_basis}</div> }));

beforeEach(() => {
  Object.values(marketDataApi).forEach((method) => method.mockReset().mockResolvedValue({ data: [] }));
  marketDataApi.getPriceMatrix.mockImplementation(async ({ basis }) => ({ data: [], price_basis: basis }));
});

it('requests separate matrices when the user changes the tax basis', async () => {
  render(<MemoryRouter><DashboardPage /></MemoryRouter>);
  await screen.findByText('tax_included');
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Cơ sở so sánh giá' }));
  fireEvent.click(screen.getByTitle('Giá chưa gồm thuế'));
  await screen.findByText('tax_excluded');
  await waitFor(() => expect(marketDataApi.getPriceMatrix).toHaveBeenLastCalledWith({
    reference_source: 'internal', basis: 'tax_excluded',
  }));
});
