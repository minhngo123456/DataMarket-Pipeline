import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PriceCell } from './PriceCell.jsx';

const cell = { source_id: 'tiki', source_name: 'Tiki', price: 120000, observed_at: new Date().toISOString(), change_percent: -4, stale: false };

describe('PriceCell', () => {
  it('marks the lowest price', () => {
    render(<PriceCell cell={cell} isMin />);
    expect(screen.getByTestId('price-cell')).toHaveClass('price-cell-min');
    expect(screen.getByText('GIÁ THẤP')).toBeInTheDocument();
    expect(screen.getByText('-4%')).toBeInTheDocument();
  });
  it('marks the highest price', () => {
    render(<PriceCell cell={cell} isMax />);
    expect(screen.getByTestId('price-cell')).toHaveClass('price-cell-max');
  });
  it('labels stale data', () => {
    render(<PriceCell cell={{ ...cell, stale: true }} />);
    expect(screen.getByText(/Quá hạn/)).toBeInTheDocument();
  });
  it('shows a dash when the source has no price', () => {
    render(<PriceCell cell={undefined} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });
});
