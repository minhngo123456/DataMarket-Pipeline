import { describe, expect, it } from 'vitest';
import { formatPercent, formatVND, toNumber } from './priceFormat.js';

const plain = (text) => text.replace(/\s/g, ' ');

describe('formatVND', () => {
  it('formats Vietnamese dong', () => {
    expect(plain(formatVND(125000))).toBe('125.000 ₫');
    expect(plain(formatVND(0))).toBe('0 ₫');
  });
  it('accepts numeric strings from DynamoDB decimals', () => {
    expect(plain(formatVND('20100000'))).toBe('20.100.000 ₫');
  });
  it('shows a dash for missing prices', () => {
    expect(formatVND(null)).toBe('—');
    expect(formatVND(undefined)).toBe('—');
    expect(formatVND('abc')).toBe('—');
  });
});

describe('formatPercent and toNumber', () => {
  it('signs percentages', () => {
    expect(formatPercent(4)).toBe('+4%');
    expect(formatPercent('-2.5')).toBe('-2,5%');
    expect(formatPercent(null)).toBe('—');
  });
  it('parses numbers', () => {
    expect(toNumber('16.00')).toBe(16);
    expect(toNumber('')).toBeNull();
  });
});
