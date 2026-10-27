import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { marketDataApi } from '../../services/marketDataApi.js';
import { CatalogPage } from './CatalogPage.jsx';

vi.mock('../../services/marketDataApi.js', () => ({
  marketDataApi: { getProducts: vi.fn(), getSources: vi.fn(), matchProduct: vi.fn() },
}));

const products = {
  data: [
    { product_id: 'phone', name: 'Phone', match_status: 'confirmed' },
    { observation_id: 'obs1', product_id: null, source_sku: 'SKU-1', price: 100, match_status: 'pending', source_id: 'vendor' },
    { observation_id: 'obs2', product_id: null, source_sku: 'SKU-2', price: 200, match_status: 'pending', source_id: 'vendor' },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  marketDataApi.getProducts.mockResolvedValue(structuredClone(products));
  marketDataApi.getSources.mockResolvedValue({ data: [{ source_id: 'vendor', name: 'Vendor CSV' }] });
  marketDataApi.matchProduct.mockResolvedValue({});
});

const row = (sku) => screen.getByText(sku).closest('tr');

describe('CatalogPage', () => {
  it('lists pending SKUs with the pending count', async () => {
    render(<CatalogPage />);
    expect(await screen.findByText('SKU-1')).toBeInTheDocument();
    expect(screen.getByText('SKU-2')).toBeInTheDocument();
    expect(screen.queryByText('Phone')).not.toBeInTheDocument();
  });

  it('approves into the chosen catalog product', async () => {
    render(<CatalogPage />);
    await screen.findByText('SKU-2');
    fireEvent.click(within(row('SKU-2')).getByRole('button', { name: /Duyệt/ }));
    const dialog = await screen.findByRole('dialog');
    const combobox = within(dialog).getByRole('combobox');
    fireEvent.mouseDown(combobox);
    fireEvent.change(combobox, { target: { value: 'Phone' } });
    fireEvent.keyDown(combobox, { key: 'Enter', code: 'Enter', keyCode: 13 });
    fireEvent.click(within(dialog).getByRole('button', { name: /Ghép vào sản phẩm/ }));
    await waitFor(() => expect(marketDataApi.matchProduct).toHaveBeenCalledWith('phone', { observation_id: 'obs2', product_id: 'phone', action: 'approve' }));
  });

  it('sends reject with the observation id and moves the row out of pending', async () => {
    render(<CatalogPage />);
    await screen.findByText('SKU-1');
    fireEvent.click(within(row('SKU-1')).getByRole('button', { name: /Từ chối/ }));
    const confirm = await waitFor(() => {
      const dialog = document.querySelector('.ant-modal-confirm');
      expect(dialog).not.toBeNull();
      return dialog;
    });
    fireEvent.click(within(confirm).getByRole('button', { name: /Từ chối/ }));
    await waitFor(() => expect(marketDataApi.matchProduct).toHaveBeenCalledWith('obs1', { observation_id: 'obs1', product_id: null, action: 'reject' }));
    await waitFor(() => expect(screen.queryByText('SKU-1')).not.toBeInTheDocument());
  });
});
