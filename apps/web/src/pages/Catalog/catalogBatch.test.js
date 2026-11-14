import { describe, expect, it, vi } from 'vitest';
import { applyBatch, batchGroups } from './catalogBatch.js';

const rows = [
  { observation_id: 'o1', source_id: 'a', source_sku: '123', source_name: 'Pen', source_variant: 'Blue' },
  { observation_id: 'o2', source_id: 'a', source_sku: '123', source_name: 'Pen', source_variant: 'Blue' },
  { observation_id: 'o3', source_id: 'b', source_sku: '123', source_name: 'Pen', source_variant: 'Red' },
];

describe('catalog bulk review', () => {
  it('groups repeated observations but never merges shops or variants by title', () => {
    const groups = batchGroups(rows);
    expect(groups).toHaveLength(2);
    expect(groups[0].rows).toHaveLength(2);
    expect(groups[0].name).toBe('Pen - Blue');
    expect(groups[0].product_id).not.toBe(groups[1].product_id);
    expect(batchGroups([rows[0], { ...rows[0], source_generation: 'replacement' }])).toHaveLength(2);
  });

  it('creates each product once and preserves successful rows when another fails', async () => {
    const groups = batchGroups(rows);
    const create = vi.fn().mockResolvedValue({});
    const match = vi.fn().mockResolvedValueOnce({}).mockRejectedValueOnce(new Error('expired')).mockResolvedValueOnce({});
    const created = vi.fn();
    const settled = vi.fn();
    const result = await applyBatch(groups, { create, match, created, settled });
    expect(create).toHaveBeenCalledTimes(2);
    expect(match).toHaveBeenCalledTimes(3);
    expect(settled).toHaveBeenCalledTimes(2);
    expect(result.accepted).toBe(2);
    expect(result.failures[0].row.observation_id).toBe('o2');
  });

  it('uses an explicitly chosen catalog target without creating or guessing a match', async () => {
    const groups = batchGroups(rows.slice(0, 2)).map((group) => ({ ...group, target: 'shared-pen' }));
    const create = vi.fn();
    const match = vi.fn().mockResolvedValue({});
    const result = await applyBatch(groups, { create, match, created: vi.fn(), settled: vi.fn() });
    expect(create).not.toHaveBeenCalled();
    expect(result.accepted).toBe(2);
    expect(match).toHaveBeenCalledWith('shared-pen', { observation_id: 'o1', product_id: 'shared-pen', action: 'approve' });
  });

  it('does not silently attach to a colliding product when creation fails', async () => {
    const match = vi.fn();
    const result = await applyBatch(batchGroups(rows.slice(0, 2)), {
      create: vi.fn().mockRejectedValue(new Error('quota or id conflict')),
      match, created: vi.fn(), settled: vi.fn(),
    });
    expect(result.accepted).toBe(0);
    expect(result.failures[0].count).toBe(2);
    expect(match).not.toHaveBeenCalled();
  });
});
