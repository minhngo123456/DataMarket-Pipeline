import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { marketDataApi } from '../../services/marketDataApi.js';
import { SourceListPage } from './SourceListPage.jsx';

vi.mock('../../services/marketDataApi.js', () => ({
  marketDataApi: { getSources: vi.fn(), createSource: vi.fn(), updateSource: vi.fn(), deleteSource: vi.fn() },
}));

const admin = { role: 'admin', tenant: { max_sources: 3 } };

beforeEach(() => {
  vi.clearAllMocks();
  marketDataApi.getSources.mockResolvedValue({ data: [] });
  marketDataApi.createSource.mockResolvedValue({});
});

async function openDialog() {
  render(<SourceListPage user={admin} />);
  await screen.findByText('Chưa cấu hình nguồn nào.');
  fireEvent.click(screen.getByRole('button', { name: /Thêm nguồn/ }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText('Tên hiển thị'), { target: { value: 'Đối thủ A' } });
  fireEvent.change(within(dialog).getByLabelText('Mã nguồn'), { target: { value: 'competitor-a' } });
  return dialog;
}

function chooseKind(dialog, label) {
  fireEvent.mouseDown(within(dialog).getByRole('combobox'));
  fireEvent.click(screen.getByTitle(label));
}

const submit = (dialog) => fireEvent.click(within(dialog).getByRole('button', { name: 'Thêm' }));

describe('SourceListPage', () => {
  it('does not ask a CSV source for a URL', async () => {
    const dialog = await openDialog();
    expect(within(dialog).queryByLabelText('Địa chỉ URL')).not.toBeInTheDocument();
    submit(dialog);
    await waitFor(() => expect(marketDataApi.createSource).toHaveBeenCalledWith({ name: 'Đối thủ A', source_id: 'competitor-a', kind: 'csv', enabled: true }));
  });

  it('sends endpoint and parsed headers for an API source', async () => {
    const dialog = await openDialog();
    chooseKind(dialog, 'REST API');
    const url = await within(dialog).findByLabelText('Địa chỉ URL');
    fireEvent.change(url, { target: { value: 'https://api.example.vn/prices' } });
    fireEvent.change(within(dialog).getByLabelText('Header HTTP (JSON)'), { target: { value: '{"User-Agent": "MarketData"}' } });
    submit(dialog);
    await waitFor(() => expect(marketDataApi.createSource).toHaveBeenCalledWith({
      name: 'Đối thủ A',
      source_id: 'competitor-a',
      kind: 'api',
      endpoint: 'https://api.example.vn/prices',
      enabled: true,
      headers: { 'User-Agent': 'MarketData' },
    }));
  });

  it('blocks a web source without a valid URL or with invalid headers', async () => {
    const dialog = await openDialog();
    chooseKind(dialog, 'Trang web tĩnh');
    const url = await within(dialog).findByLabelText('Địa chỉ URL');
    fireEvent.change(url, { target: { value: 'file:///etc/passwd' } });
    fireEvent.change(within(dialog).getByLabelText('Header HTTP (JSON)'), { target: { value: '["x"]' } });
    submit(dialog);
    expect(await within(dialog).findByText('URL phải bắt đầu bằng http:// hoặc https://.')).toBeInTheDocument();
    expect(await within(dialog).findByText('Nhập JSON dạng {"Tên-header": "giá trị"}.')).toBeInTheDocument();
    expect(marketDataApi.createSource).not.toHaveBeenCalled();
  });
});
