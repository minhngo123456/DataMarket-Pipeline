import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { marketDataApi } from '../../services/marketDataApi.js';
import { SourceListPage } from './SourceListPage.jsx';

vi.mock('../../services/marketDataApi.js', () => ({
  marketDataApi: { getSources: vi.fn(), createSource: vi.fn(), updateSource: vi.fn(), deleteSource: vi.fn() },
}));

vi.mock('../../mocks/data.js', () => ({
  mockSources: {
    data: [{ source_id: 'competitor-a', name: 'Nguồn mẫu ban đầu', kind: 'csv', enabled: true }],
  },
}));

const admin = { role: 'admin', tenant: { max_sources: 3 } };

beforeEach(() => {
  vi.clearAllMocks();
  Object.values(marketDataApi).forEach((method) => method.mockReset());
  marketDataApi.getSources.mockResolvedValue({ data: [] });
  marketDataApi.createSource.mockImplementation(async (payload) => payload.collect_now ? { job_id: 'job-now', collection_status: 'queued' } : {});
  marketDataApi.updateSource.mockImplementation(async (_, payload) => payload.collect_now ? { job_id: 'job-now', collection_status: 'queued' } : {});
  marketDataApi.deleteSource.mockResolvedValue({});
});

async function fillDialog() {
  fireEvent.click(screen.getByRole('button', { name: /Thêm nguồn/ }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText('Tên hiển thị'), { target: { value: 'Đối thủ A' } });
  fireEvent.change(within(dialog).getByLabelText('Mã nguồn'), { target: { value: 'competitor-a' } });
  return dialog;
}

async function openDialog() {
  render(<SourceListPage user={admin} />);
  await screen.findByText('Chưa cấu hình nguồn nào.');
  return fillDialog();
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
      collect_now: true,
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

  it('requires an explicit tax basis and accepts the Phuc Hy homepage URL', async () => {
    const dialog = await openDialog();
    chooseKind(dialog, 'Trang web tĩnh');
    expect(await within(dialog).findByText(/https:\/\/phuchy.vn\/ để lấy tối đa 10 sản phẩm/)).toBeInTheDocument();
    fireEvent.change(await within(dialog).findByLabelText('Địa chỉ URL'), { target: { value: 'https://phuchy.vn/' } });
    submit(dialog);
    expect(await within(dialog).findByText('Chọn cách tính thuế của giá nguồn.')).toBeInTheDocument();
    expect(marketDataApi.createSource).not.toHaveBeenCalled();
    fireEvent.mouseDown(within(dialog).getByLabelText('Cách tính thuế của giá nguồn'));
    fireEvent.click(screen.getByTitle('Giá chưa gồm thuế'));
    submit(dialog);
    await waitFor(() => expect(marketDataApi.createSource).toHaveBeenCalledWith({
      name: 'Đối thủ A', source_id: 'competitor-a', kind: 'web', enabled: true,
      endpoint: 'https://phuchy.vn/', price_basis: 'tax_excluded', collect_now: true,
    }));
  });

  it('collects an existing web source immediately and refreshes its real status', async () => {
    const source = { source_id: 'phuc-hy', name: 'Phục Hy', kind: 'web', enabled: true, url: 'https://phuchy.vn/' };
    marketDataApi.getSources.mockResolvedValueOnce({ data: [source] })
      .mockResolvedValueOnce({ data: [{ ...source, collection_status: 'queued', last_job_id: 'job-now' }] })
      .mockResolvedValue({ data: [{ ...source, collection_status: 'succeeded', last_job_id: 'job-now' }] });
    marketDataApi.updateSource.mockResolvedValue({ job_id: 'job-now', collection_status: 'queued' });
    render(<SourceListPage user={admin} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Thu thập Phục Hy' }));
    expect(await screen.findByText('Chờ xử lý')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Thu thập Phục Hy' })).toBeDisabled();
    expect(marketDataApi.updateSource).toHaveBeenCalledWith('phuc-hy', { collect_now: true });
    expect(await screen.findByText('Đã thu thập', {}, { timeout: 10000 })).toBeInTheDocument();
  });

  it('enabling a web source also requests collection', async () => {
    const source = { source_id: 'phuc-hy', name: 'Phục Hy', kind: 'web', enabled: false };
    marketDataApi.getSources.mockResolvedValue({ data: [source] });
    render(<SourceListPage user={admin} />);
    fireEvent.click(await screen.findByRole('switch', { name: 'Bật/tắt Phục Hy' }));
    await waitFor(() => expect(marketDataApi.updateSource).toHaveBeenCalledWith('phuc-hy', { enabled: true, collect_now: true }));
  });

  it('reports an old backend that saves a source without queueing a collection', async () => {
    marketDataApi.createSource.mockResolvedValue({ status: 'configured' });
    const dialog = await openDialog();
    chooseKind(dialog, 'REST API');
    fireEvent.change(await within(dialog).findByLabelText('Địa chỉ URL'), { target: { value: 'https://api.example.invalid/prices' } });
    submit(dialog);
    expect(await screen.findByText(/backend chưa xác nhận job thu thập/)).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByText('Đã gửi yêu cầu thu thập ngay. Trạng thái sẽ tự cập nhật.')).not.toBeInTheDocument();
  });

  it('reloads the authoritative list when POST updates an existing source ID', async () => {
    const original = { source_id: 'competitor-a', name: 'Tên nguồn cũ', kind: 'csv', enabled: true };
    const other = { source_id: 'other-source', name: 'Nguồn khác', kind: 'csv', enabled: true };
    const updated = { ...original, name: 'Tên đã lưu trên máy chủ', enabled: false };
    marketDataApi.getSources.mockResolvedValueOnce({ data: [original, other] })
      .mockResolvedValue({ data: [updated, other] });
    render(<SourceListPage user={admin} />);
    await screen.findByText(original.name);

    submit(await fillDialog());

    await screen.findByText(updated.name);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(marketDataApi.createSource).toHaveBeenCalledTimes(1);
    expect(marketDataApi.getSources).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(original.name)).not.toBeInTheDocument();
    expect(within(screen.getByRole('table')).getAllByText(original.source_id)).toHaveLength(1);
    expect(screen.getByRole('switch', { name: `Bật/tắt ${updated.name}` })).not.toBeChecked();
    expect(screen.getByRole('button', { name: /Thêm nguồn/ })).toBeEnabled();
    expect(screen.queryByText('Đã dùng hết 3 nguồn của gói Pilot.')).not.toBeInTheDocument();
  });

  it.each(['PATCH', 'DELETE'])('reloads a conflicted %s without automatically retrying it', async (method) => {
    const original = { source_id: 'competitor-a', name: 'Cấu hình trước xung đột', kind: 'csv', enabled: true };
    const changed = { ...original, name: 'Cấu hình mới của người khác', enabled: false };
    const mutation = method === 'PATCH' ? marketDataApi.updateSource : marketDataApi.deleteSource;
    mutation.mockRejectedValue({ response: {
      status: 409, data: { error: { code: 'conflict', message: 'source_configuration_changed' } },
    } });
    marketDataApi.getSources.mockResolvedValueOnce({ data: [original] })
      .mockResolvedValue({ data: [changed] });
    render(<SourceListPage user={admin} />);
    await screen.findByText(original.name);

    if (method === 'PATCH') {
      fireEvent.click(screen.getByRole('switch', { name: `Bật/tắt ${original.name}` }));
    } else {
      fireEvent.click(screen.getByRole('button', { name: `Xóa ${original.name}` }));
      fireEvent.click(await screen.findByRole('button', { name: 'Xóa', exact: true }));
    }

    await screen.findByText(changed.name);
    expect(await screen.findByText('Thao tác xung đột với cấu hình nguồn hiện tại. Đang tải lại danh sách; hãy kiểm tra trước khi thử lại.')).toBeInTheDocument();
    expect(mutation).toHaveBeenCalledTimes(1);
    expect(marketDataApi.getSources).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(original.name)).not.toBeInTheDocument();
    expect(screen.getByRole('switch', { name: `Bật/tắt ${changed.name}` })).not.toBeChecked();
  });

  it('updates a mock source with the same ID without adding a duplicate or calling mutations', async () => {
    marketDataApi.getSources.mockRejectedValue(new Error('Local API unavailable'));
    render(<SourceListPage user={admin} />);
    await screen.findByText('Nguồn mẫu ban đầu');

    submit(await fillDialog());

    await screen.findByText('Đối thủ A');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(within(screen.getByRole('table')).getAllByText('competitor-a')).toHaveLength(1);
    expect(screen.queryByText('Nguồn mẫu ban đầu')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Thêm nguồn/ })).toBeEnabled();
    expect(marketDataApi.createSource).not.toHaveBeenCalled();
    expect(marketDataApi.getSources).toHaveBeenCalledTimes(1);
  });
});
