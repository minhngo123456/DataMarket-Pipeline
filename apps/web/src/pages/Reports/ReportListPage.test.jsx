import { configure, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/marketDataApi.js', () => ({
  marketDataApi: { getReports: vi.fn().mockResolvedValue({ data: [] }), triggerReport: vi.fn() },
}));

async function renderAs(role, schedule) {
  vi.resetModules();
  vi.doMock('../../config.js', () => ({ mockFallbackEnabled: false, reportScheduleEnabled: schedule }));
  const { ReportListPage } = await import('./ReportListPage.jsx');
  render(<MemoryRouter><ReportListPage user={{ role }} /></MemoryRouter>);
}

// Each test re-imports the page after resetModules, which is slow when the whole suite runs in
// parallel; give the import and the findBy queries room instead of failing on a busy machine.
configure({ asyncUtilTimeout: 10000 });

describe('ReportListPage', { timeout: 60000 }, () => {
  beforeEach(() => vi.doUnmock('../../config.js'));

  it('replaces the manual button with a note while the 08:00 schedule runs', async () => {
    await renderAs('admin', true);
    expect(await screen.findByText('Báo cáo được tạo tự động lúc 08:00 mỗi ngày')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Tạo báo cáo ngay/ })).toBeNull();
  });

  it('keeps the manual button when there is no schedule', async () => {
    await renderAs('analyst', false);
    expect(await screen.findByRole('button', { name: /Tạo báo cáo ngay/ })).toBeInTheDocument();
  });

  it('shows neither to a viewer', async () => {
    await renderAs('viewer', false);
    await screen.findByText(/tóm tắt bởi AI/);
    expect(screen.queryByRole('button', { name: /Tạo báo cáo ngay/ })).toBeNull();
    expect(screen.queryByText('Báo cáo được tạo tự động lúc 08:00 mỗi ngày')).toBeNull();
  });
});
