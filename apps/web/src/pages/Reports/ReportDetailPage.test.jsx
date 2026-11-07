import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { marketDataApi } from '../../services/marketDataApi.js';
import { ReportDetailPage } from './ReportDetailPage.jsx';

vi.mock('../../services/marketDataApi.js', () => ({ marketDataApi: { getReport: vi.fn() } }));

function renderReport(report) {
  marketDataApi.getReport.mockResolvedValue({ report_id: 'r1', status: 'ready', ai_status: 'ai_generated', metrics: {}, ...report });
  const { container } = render(
    <MemoryRouter initialEntries={['/reports/r1']}>
      <Routes><Route path="/reports/:id" element={<ReportDetailPage />} /></Routes>
    </MemoryRouter>,
  );
  return container;
}

describe('ReportDetailPage', () => {
  it('shows the whole report text, past the 1000-character preview', async () => {
    const ending = 'Câu cuối của báo cáo.';
    renderReport({ summary: `${'Nhận định. '.repeat(150)}${ending}` });
    expect(await screen.findByText(new RegExp(ending))).toBeInTheDocument();
  });

  it('renders simple markdown without running HTML from the model', async () => {
    const container = renderReport({ summary: '**Khuyến nghị**\n\n- Theo dõi giá\n\n<img src=x onerror="alert(1)"><script>alert(1)</script>' });
    expect((await screen.findByText('Khuyến nghị')).tagName).toBe('STRONG');
    expect(screen.getByText('Theo dõi giá').closest('li')).not.toBeNull();
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
  });
});
