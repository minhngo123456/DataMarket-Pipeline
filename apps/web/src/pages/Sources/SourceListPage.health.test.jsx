import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { marketDataApi } from '../../services/marketDataApi.js';
import { SourceListPage } from './SourceListPage.jsx';

vi.mock('../../services/marketDataApi.js', () => ({
  marketDataApi: { getSources: vi.fn(), createSource: vi.fn(), updateSource: vi.fn(), deleteSource: vi.fn() },
}));

const viewer = { role: 'viewer', tenant: { max_sources: 3 } };
const makeSource = (source_id, fields = {}) => ({
  source_id, name: `Nguồn ${source_id}`, kind: 'api', enabled: true, ...fields,
});
const sourceRow = (source) => screen.getByText(source.name).closest('tr');

beforeEach(() => {
  Object.values(marketDataApi).forEach((method) => method.mockReset());
  marketDataApi.getSources.mockResolvedValue({ data: [] });
});

async function showSources(sources, user = viewer) {
  marketDataApi.getSources.mockResolvedValue({ data: sources });
  render(<SourceListPage user={user} />);
  await screen.findByText(sources[0].name, {}, { timeout: 5000 });
}

describe('SourceListPage collection health', () => {
  it('uses collection state rather than inferring success from configuration or missing health', async () => {
    const cases = [
      [makeSource('never-run'), 'Chưa thu thập'],
      [makeSource('null-state', { collection_status: null }), 'Chưa thu thập'],
      [makeSource('empty-state', { collection_status: '' }), 'Chưa thu thập'],
      [makeSource('running', { collection_status: 'running' }), 'Đang thu thập'],
      [makeSource('retrying', { collection_status: 'retrying' }), 'Chờ thử lại'],
      [makeSource('succeeded', { collection_status: 'succeeded' }), 'Đã thu thập'],
      [makeSource('failed', { collection_status: 'failed' }), 'Thu thập lỗi'],
      [makeSource('unknown', { collection_status: 'future-state' }), 'Chưa xác định'],
      [makeSource('prototype-name', { collection_status: 'constructor' }), 'Chưa xác định'],
    ];

    await showSources(cases.map(([source]) => source));

    for (const [source, label] of cases) {
      expect(within(sourceRow(source)).getByText(label, { exact: true })).toBeInTheDocument();
    }
    expect(screen.queryByText('future-state')).not.toBeInTheDocument();
  });

  it('shows attempt and success timestamps in Vietnam time and leaves missing history unrecorded', async () => {
    const collected = makeSource('dated', {
      collection_status: 'succeeded',
      last_attempt_at: '2026-11-06T01:00:00Z',
      last_success_at: '2026-11-06T01:02:00Z',
    });
    const neverRun = makeSource('no-history', { updated_at: '2026-11-06T01:10:00Z' });
    const invalidHistory = makeSource('invalid-history', {
      last_attempt_at: 'invalid-attempt-date', last_success_at: 'invalid-success-date',
    });

    await showSources([collected, neverRun, invalidHistory]);

    const history = within(sourceRow(collected));
    expect(history.getByText(/Lần chạy/)).toBeInTheDocument();
    expect(history.getByText(/Thành công gần nhất/)).toBeInTheDocument();
    expect(history.getByText('06/11/2026 08:00')).toBeInTheDocument();
    expect(history.getByText('06/11/2026 08:02')).toBeInTheDocument();
    expect(within(sourceRow(neverRun)).getAllByText('Chưa ghi nhận')).toHaveLength(2);
    expect(within(sourceRow(neverRun)).getByText('Chưa thu thập')).toBeInTheDocument();
    expect(within(sourceRow(invalidHistory)).getAllByText('Chưa ghi nhận')).toHaveLength(2);
    expect(within(sourceRow(invalidHistory)).queryByText(/Invalid Date/)).not.toBeInTheDocument();
  });

  it.each([
    ['retrying', 'Chờ thử lại', 'api_http_retryable_429'],
    ['failed', 'Thu thập lỗi', 'api_http_error_403'],
  ])('retains previous success when the latest collection is %s', async (status, label, error) => {
    const source = makeSource(status, {
      collection_status: status,
      last_attempt_at: '2026-11-06T01:00:00Z',
      last_success_at: '2026-11-05T18:00:00Z',
      last_error_code: error,
    });

    await showSources([source]);

    const row = within(sourceRow(source));
    expect(row.getByText(label)).toBeInTheDocument();
    expect(row.getByText('06/11/2026 01:00')).toBeInTheDocument();
    expect(row.getByText(error)).toBeInTheDocument();
  });

  it('keeps disabled state separate from the last collection result', async () => {
    const source = makeSource('disabled', {
      enabled: false,
      collection_status: 'succeeded',
      last_success_at: '2026-11-06T01:02:00Z',
    });

    await showSources([source]);

    const row = within(sourceRow(source));
    expect(row.getByText('Đã tắt')).toBeInTheDocument();
    expect(row.getByText('Đã thu thập')).toBeInTheDocument();
    expect(row.getByText('06/11/2026 08:02')).toBeInTheDocument();
  });

  it('hides obsolete error codes once a source is running or succeeded', async () => {
    const sources = ['running', 'succeeded'].map((status) => makeSource(status, {
      collection_status: status,
      last_error_code: 'api_http_error_403',
    }));

    await showSources(sources);

    expect(screen.queryByText('api_http_error_403')).not.toBeInTheDocument();
  });

  it('does not render an unsafe upstream error value', async () => {
    const unsafeError = 'https://upstream.example.invalid/prices?token=fake-test-token';
    const source = makeSource('unsafe-error', {
      collection_status: 'failed', last_error_code: unsafeError,
    });

    await showSources([source]);

    expect(within(sourceRow(source)).getByText('Thu thập lỗi')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(unsafeError);
    expect(document.body.innerHTML).not.toContain('fake-test-token');
  });

  it('lets a Viewer refresh collection health without changing sources or treating collection as normalization', async () => {
    const original = makeSource('refresh', { collection_status: 'running' });
    const updated = {
      ...original,
      collection_status: 'succeeded',
      last_success_at: '2026-11-06T01:02:00Z',
    };
    marketDataApi.getSources.mockResolvedValueOnce({ data: [original] })
      .mockResolvedValue({ data: [updated] });
    render(<SourceListPage user={viewer} />);
    await screen.findByText(original.name, {}, { timeout: 5000 });
    expect(within(sourceRow(original)).getByText('Đang thu thập')).toBeInTheDocument();

    const refresh = screen.getByRole('button', { name: /Làm mới/ });
    await waitFor(() => expect(refresh).not.toHaveClass('ant-btn-loading'), { timeout: 5000 });
    fireEvent.click(refresh);

    await waitFor(() => expect(marketDataApi.getSources).toHaveBeenCalledTimes(2), { timeout: 5000 });
    await waitFor(() => expect(within(sourceRow(updated)).getByText('Đã thu thập')).toBeInTheDocument(), { timeout: 5000 });
    expect(marketDataApi.createSource).not.toHaveBeenCalled();
    expect(marketDataApi.updateSource).not.toHaveBeenCalled();
    expect(marketDataApi.deleteSource).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /Thêm nguồn/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
    expect(screen.getByText(/đã thu thập.*chuẩn hóa/i)).toBeInTheDocument();
  });
});
