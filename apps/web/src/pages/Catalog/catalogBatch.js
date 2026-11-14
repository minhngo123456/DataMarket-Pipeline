export function sourceProductName(row) {
  const variant = row.source_variant && row.source_variant !== 'Default Title' ? row.source_variant : '';
  return [row.source_name, variant].filter(Boolean).join(' - ');
}

// Scope suggestions by source: equal titles/SKUs from different shops are not
// proof of the same product or selling unit. Users can explicitly select a shared product.
export function batchGroups(rows) {
  const groups = new Map();
  for (const row of rows) {
    const key = JSON.stringify([row.source_id, row.source_generation || '', row.source_sku]);
    if (!groups.has(key)) {
      const text = `${row.source_id}-${row.source_generation || 'legacy'}-${row.source_sku}`;
      let hash = 2166136261;
      for (const char of text) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619) >>> 0;
      const prefix = text.toLowerCase().replace(/[^a-z0-9_-]+/g, '-').slice(0, 65);
      groups.set(key, { key, rows: [], name: sourceProductName(row),
        product_id: `source-${prefix}-${hash.toString(16)}`, target: null });
    }
    if (!groups.get(key).name) groups.get(key).name = sourceProductName(row);
    groups.get(key).rows.push(row);
  }
  return [...groups.values()];
}

export async function applyBatch(groups, { create, match, created, settled, progress }) {
  const failures = [];
  let accepted = 0;
  for (const group of groups) {
    let productId = group.target;
    try {
      if (!productId) {
        if (!group.name.trim()) throw new Error('Thiếu tên sản phẩm; hãy nhập tên hoặc chọn sản phẩm có sẵn.');
        productId = group.product_id;
        const product = { product_id: productId, name: group.name.trim() };
        await create(product);
        created(product, group.key);
      }
    } catch (error) {
      failures.push({ group, error, count: group.rows.length });
      progress?.(accepted, failures);
      continue;
    }
    for (const row of group.rows) {
      try {
        await match(productId, { observation_id: row.observation_id, product_id: productId, action: 'approve' });
        settled(row, productId);
        accepted += 1;
      } catch (error) {
        failures.push({ group, row, error, count: 1 });
      }
      progress?.(accepted, failures);
    }
  }
  return { accepted, failures };
}
