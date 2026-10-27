import dayjs from 'dayjs';
import 'dayjs/locale/vi';
import relativeTime from 'dayjs/plugin/relativeTime';
import timezone from 'dayjs/plugin/timezone';
import utc from 'dayjs/plugin/utc';

dayjs.extend(relativeTime);
dayjs.extend(utc);
dayjs.extend(timezone);
dayjs.locale('vi');

export const TIME_ZONE = 'Asia/Ho_Chi_Minh';
export const STALE_HOURS = 24;

const parse = (value) => dayjs.utc(value).tz(TIME_ZONE);

/** UTC ISO string → "3 giờ trước". */
export function timeAgo(value, now = Date.now()) {
  if (!value) return '—';
  return parse(value).from(dayjs(now));
}

/** UTC ISO string → "20/10/2026 08:00" in Asia/Ho_Chi_Minh. */
export function formatDateTime(value) {
  if (!value) return '—';
  return parse(value).format('DD/MM/YYYY HH:mm');
}

/** UTC ISO string → "2026-10-20", the local calendar day. */
export function localDay(value) {
  return parse(value).format('YYYY-MM-DD');
}

/** Data older than 24 hours is stale and must be labelled as such. */
export function isStale(value, now = Date.now()) {
  if (!value) return true;
  return dayjs(now).diff(dayjs.utc(value), 'hour', true) > STALE_HOURS;
}
