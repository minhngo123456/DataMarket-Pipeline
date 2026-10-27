import { toNumber } from './priceFormat.js';
import { isStale, localDay } from './timeHelpers.js';

export const ANOMALY_PERCENT = 15;

/**
 * Pivot GET /matrix rows (one row per product × source) into table rows with
 * one cell per source. The reference source comes first, then the others by name.
 */
export function buildMatrix(rows = [], { products = [], sources = [], referenceSource = 'internal', now = Date.now() } = {}) {
  const productNames = Object.fromEntries(
    products.filter((p) => p.product_id && p.name).map((p) => [p.product_id, p.name]),
  );
  const sourceNames = Object.fromEntries(sources.map((s) => [s.source_id, s.name || s.source_id]));
  const sourceIds = new Set(sources.filter((s) => s.enabled !== false).map((s) => s.source_id));
  rows.forEach((row) => sourceIds.add(row.source_id));

  const columns = [...sourceIds]
    .map((id) => ({ source_id: id, name: sourceNames[id] || id, isReference: id === referenceSource }))
    .sort((a, b) => Number(b.isReference) - Number(a.isReference) || a.name.localeCompare(b.name, 'vi'));

  const byProduct = new Map();
  rows.forEach((row) => {
    const price = toNumber(row.price);
    if (price == null) return;
    if (!byProduct.has(row.product_id)) {
      byProduct.set(row.product_id, {
        product_id: row.product_id,
        name: productNames[row.product_id] || row.product_id,
        cells: {},
      });
    }
    byProduct.get(row.product_id).cells[row.source_id] = {
      source_id: row.source_id,
      source_name: sourceNames[row.source_id] || row.source_id,
      price,
      observed_at: row.observed_at,
      change_percent: toNumber(row.change_percent),
      stale: isStale(row.observed_at, now),
    };
  });

  const products_ = [...byProduct.values()].map((product) => {
    // Stale prices are shown but never take part in the comparison.
    const fresh = Object.values(product.cells).filter((cell) => !cell.stale).map((cell) => cell.price);
    const min = fresh.length > 1 ? Math.min(...fresh) : null;
    const max = fresh.length > 1 ? Math.max(...fresh) : null;
    const anomaly = Object.values(product.cells).some(
      (cell) => cell.change_percent != null && Math.abs(cell.change_percent) >= ANOMALY_PERCENT,
    );
    return { ...product, min: min === max ? null : min, max: min === max ? null : max, anomaly };
  });
  products_.sort((a, b) => a.name.localeCompare(b.name, 'vi'));
  return { columns, products: products_ };
}

/** Group GET /products/:id/price-history rows into one chart point per local day. */
export function buildHistorySeries(rows = [], sourceNames = {}) {
  const series = new Set();
  const days = new Map();
  [...rows]
    .sort((a, b) => String(a.observed_at).localeCompare(String(b.observed_at)))
    .forEach((row) => {
      const price = toNumber(row.price);
      if (price == null || !row.observed_at) return;
      const name = sourceNames[row.source_id] || row.source_id;
      const day = localDay(row.observed_at);
      series.add(name);
      if (!days.has(day)) days.set(day, { date: day });
      days.get(day)[name] = price; // latest observation of the day wins
    });
  return {
    series: [...series],
    points: [...days.values()].sort((a, b) => a.date.localeCompare(b.date)),
  };
}
