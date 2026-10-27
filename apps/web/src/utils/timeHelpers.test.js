import { describe, expect, it } from 'vitest';
import { formatDateTime, isStale, localDay, timeAgo } from './timeHelpers.js';

const NOW = Date.parse('2026-10-20T03:00:00Z');

describe('time helpers', () => {
  it('shows UTC times in Asia/Ho_Chi_Minh', () => {
    expect(formatDateTime('2026-10-20T01:00:00Z')).toBe('20/10/2026 08:00');
    expect(localDay('2026-10-19T18:30:00Z')).toBe('2026-10-20');
  });
  it('marks data older than 24 hours as stale', () => {
    expect(isStale('2026-10-20T02:00:00Z', NOW)).toBe(false);
    expect(isStale('2026-10-19T02:00:00Z', NOW)).toBe(true);
    expect(isStale(null, NOW)).toBe(true);
  });
  it('handles missing values', () => {
    expect(timeAgo(null)).toBe('—');
    expect(formatDateTime(undefined)).toBe('—');
    expect(timeAgo('2026-10-20T00:00:00Z', NOW)).toBe('3 giờ trước');
  });
});
