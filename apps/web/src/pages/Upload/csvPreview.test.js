import { describe, expect, it } from 'vitest';
import { MAX_CSV_ROWS, MAX_UPLOAD_BYTES, parseCsvPreview, validateCsvFile } from './csvPreview.js';

describe('validateCsvFile', () => {
  it('accepts a small CSV', () => {
    expect(validateCsvFile(new File(['a'], 'prices.csv', { type: 'text/csv' }))).toBeNull();
  });
  it('rejects Excel files', () => {
    expect(validateCsvFile(new File(['a'], 'prices.xlsx'))).toMatch(/csv/i);
  });
  it('rejects files over 1 MB', () => {
    const big = { name: 'big.csv', size: MAX_UPLOAD_BYTES + 1 };
    expect(validateCsvFile(big)).toMatch(/1 MB/);
  });
  it('accepts a file of exactly 1 MiB', () => {
    expect(validateCsvFile({ name: 'edge.csv', size: 1024 * 1024 })).toBeNull();
  });
});

describe('parseCsvPreview', () => {
  it('counts rows and reports missing required columns', async () => {
    const file = new File(['source_sku,price\nA,1\nB,2\n'], 'p.csv', { type: 'text/csv' });
    const preview = await parseCsvPreview(file);
    expect(preview.totalRows).toBe(2);
    expect(preview.missing).toEqual(['currency', 'observed_at']);
  });
  it('allows at most the row limit CSVWorker accepts', () => {
    expect(MAX_CSV_ROWS).toBe(1000);
  });
});
