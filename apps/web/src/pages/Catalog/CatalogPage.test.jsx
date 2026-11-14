import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { marketDataApi } from '../../services/marketDataApi.js';
import { CatalogPage } from './CatalogPage.jsx';

vi.mock('../../services/marketDataApi.js', () => ({
  marketDataApi: { getProducts: vi.fn(), getSources: vi.fn(), matchProduct: vi.fn(), createProduct: vi.fn() },
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
  marketDataApi.createProduct.mockResolvedValue({});
});

const row = (sku) => screen.getByText(sku).closest('tr');

describe('CatalogPage', () => {
  it('displays product names, variants, safe source link and approves all selected in one dialog', async () => {
    marketDataApi.getProducts.mockResolvedValue({ data: products.data.map((item) => item.observation_id
      ? { ...item, source_name: 'Gel pen', source_variant: item.source_sku, source_url: 'https://shop.example/pen', extraction_method: 'ai_rules' } : item) });
    render(<CatalogPage />);
    expect(await screen.findByText('Gel pen - SKU-1')).toBeInTheDocument();
    expect(screen.getAllByText('AI hỗ trợ')).toHaveLength(2);
    expect(screen.getAllByRole('link', { name: 'Xem sản phẩm gốc' })[0]).toHaveAttribute('href', 'https://shop.example/pen');
    fireEvent.click(screen.getByRole('button', { name: 'Chọn tất cả kết quả chờ duyệt' }));
    fireEvent.click(screen.getByRole('button', { name: 'Duyệt đã chọn (2)' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText('Tên SKU-1')).toHaveValue('Gel pen - SKU-1');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Xác nhận duyệt' }));
    await waitFor(() => expect(marketDataApi.matchProduct).toHaveBeenCalledTimes(2));
    expect(marketDataApi.createProduct).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
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

  it('creates a catalog product from the SKU when the catalog is empty', async () => {
    marketDataApi.getProducts.mockResolvedValue({ data: [structuredClone(products.data[1])] });
    render(<CatalogPage />);
    await screen.findByText('SKU-1');
    fireEvent.click(within(row('SKU-1')).getByRole('button', { name: /Duyệt/ }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Tên sản phẩm'), { target: { value: 'iPhone 15' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /Ghép vào sản phẩm/ }));
    await waitFor(() => expect(marketDataApi.createProduct).toHaveBeenCalledWith({ product_id: 'sku-1', name: 'iPhone 15' }));
    await waitFor(() => expect(marketDataApi.matchProduct).toHaveBeenCalledWith('sku-1', { observation_id: 'obs1', product_id: 'sku-1', action: 'approve' }));
  });

  const openOptions = async (sku) => {
    fireEvent.click(within(row(sku)).getByRole('button', { name: /Duyệt/ }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.mouseDown(within(dialog).getByRole('combobox'));
    return dialog;
  };

  it('lists only real products after another SKU was approved', async () => {
    render(<CatalogPage />);
    await screen.findByText('SKU-2');
    const dialog = await openOptions('SKU-2');
    const combobox = within(dialog).getByRole('combobox');
    fireEvent.change(combobox, { target: { value: 'Phone' } });
    fireEvent.keyDown(combobox, { key: 'Enter', code: 'Enter', keyCode: 13 });
    fireEvent.click(within(dialog).getByRole('button', { name: /Ghép vào sản phẩm/ }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    await openOptions('SKU-1');
    expect(await screen.findByTitle('Phone (phone)')).toBeInTheDocument();
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
    expect(screen.queryByTitle('phone')).not.toBeInTheDocument();
  });

  it('falls back to the product id when a product has no name', async () => {
    marketDataApi.getProducts.mockResolvedValue({
      data: [{ product_id: 'no-name', name: null, match_status: 'confirmed' }, structuredClone(products.data[1])],
    });
    render(<CatalogPage />);
    await screen.findByText('SKU-1');
    await openOptions('SKU-1');
    expect(await screen.findByTitle('no-name')).toBeInTheDocument();
    expect(screen.queryByText(/null|undefined/)).not.toBeInTheDocument();
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
