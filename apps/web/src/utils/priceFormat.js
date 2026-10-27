const vnd = new Intl.NumberFormat('vi-VN', {
  style: 'currency',
  currency: 'VND',
  maximumFractionDigits: 0,
});

// DynamoDB decimals reach the browser as numbers or numeric strings.
export function toNumber(value) {
  if (value == null || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

/** 125000 → "125.000 ₫"; missing values → "—". */
export function formatVND(value) {
  const number = toNumber(value);
  return number == null ? '—' : vnd.format(number);
}

/** -2.5 → "-2,5%"; 4 → "+4%". */
export function formatPercent(value) {
  const number = toNumber(value);
  if (number == null) return '—';
  const text = number.toLocaleString('vi-VN', { maximumFractionDigits: 2 });
  return `${number > 0 ? '+' : ''}${text}%`;
}
