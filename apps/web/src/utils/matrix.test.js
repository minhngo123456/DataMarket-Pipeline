import { describe, expect, it } from 'vitest';
import { buildHistorySeries, buildMatrix } from './matrix.js';

const NOW = Date.parse('2026-10-20T03:00:00Z');
const rows = [
  { product_id: 'p1', source_id: 'internal', price: 125000, observed_at: '2026-10-20T01:00:00Z', change_percent: 0 },
  { product_id: 'p1', source_id: 'tiki', price: '120000', observed_at: '2026-10-20T02:00:00Z', change_percent: '-4.00' },
  { product_id: 'p1', source_id: 'sendo', price: 90000, observed_at: '2026-10-18T02:00:00Z', change_percent: '-28.00' },
  { product_id: 'p2', source_id: 'internal', price: 50000, observed_at: '2026-10-20T01:00:00Z', change_percent: 0 },
];
const sources = [
  { source_id: 'tiki', name: 'Tiki', enabled: true },
  { source_id: 'internal', name: 'Nội bộ', enabled: true },
  { source_id: 'sendo', name: 'Sendo', enabled: true },
  { source_id: 'off', name: 'Tắt', enabled: false },
];

describe('buildMatrix', () => {
  const result = buildMatrix(rows, { products: [{ product_id: 'p1', name: 'Áo thun' }], sources, now: NOW });

  it('puts the reference source first and skips disabled sources without data', () => {
    expect(result.columns.map((c) => c.source_id)).toEqual(['internal', 'sendo', 'tiki']);
  });
  it('pivots rows into one product per line with names', () => {
    const p1 = result.products.find((p) => p.product_id === 'p1');
    expect(p1.name).toBe('Áo thun');
    expect(p1.cells.tiki.price).toBe(120000);
    expect(result.products.find((p) => p.product_id === 'p2').name).toBe('p2');
  });
  it('excludes stale prices from min/max but flags anomalies', () => {
    const p1 = result.products.find((p) => p.product_id === 'p1');
    expect(p1.cells.sendo.stale).toBe(true);
    expect(p1.min).toBe(120000);
    expect(p1.max).toBe(125000);
    expect(p1.anomaly).toBe(true);
  });
  it('has no min/max with a single fresh price', () => {
    const p2 = result.products.find((p) => p.product_id === 'p2');
    expect(p2.min).toBeNull();
    expect(p2.max).toBeNull();
  });
});

describe('buildHistorySeries', () => {
  it('keeps the latest price per local day and source', () => {
    const { series, points } = buildHistorySeries([
      { source_id: 'tiki', price: 100, observed_at: '2026-10-19T01:00:00Z' },
      { source_id: 'tiki', price: 90, observed_at: '2026-10-19T05:00:00Z' },
      { source_id: 'internal', price: 110, observed_at: '2026-10-19T18:00:00Z' },
    ], { tiki: 'Tiki' });
    expect(series).toEqual(['Tiki', 'internal']);
    expect(points).toEqual([{ date: '2026-10-19', Tiki: 90 }, { date: '2026-10-20', internal: 110 }]);
  });
});
