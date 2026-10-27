import Papa from 'papaparse';

export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
// Same set as services/ingestion/csv_worker.py REQUIRED; price_basis is optional.
export const REQUIRED_COLUMNS = ['source_sku', 'price', 'currency', 'observed_at'];

/** Checks a chosen file before any upload. Returns an error message or null. */
export function validateCsvFile(file) {
  if (!file) return 'Chưa chọn tệp.';
  if (!/\.csv$/i.test(file.name)) return 'Chỉ hỗ trợ tệp .csv (MVP không nhận Excel).';
  if (file.size > MAX_UPLOAD_BYTES) return 'Tệp vượt quá 10 MB.';
  return null;
}

/** Parse the whole file for a row count and missing columns, keep 5 rows for preview. */
export function parseCsvPreview(file) {
  return new Promise((resolve, reject) => {
    Papa.parse(file, {
      header: true,
      skipEmptyLines: true,
      complete: (result) => {
        const fields = result.meta.fields || [];
        resolve({
          fields,
          rows: result.data.slice(0, 5),
          totalRows: result.data.length,
          missing: REQUIRED_COLUMNS.filter((column) => !fields.includes(column)),
        });
      },
      error: reject,
    });
  });
}
