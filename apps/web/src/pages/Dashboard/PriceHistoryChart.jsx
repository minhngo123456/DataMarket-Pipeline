import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { formatVND } from '../../utils/priceFormat.js';

const COLORS = ['#3768e5', '#fa8c16', '#722ed1', '#52c41a', '#eb2f96'];

export function PriceHistoryChart({ series, points }) {
  if (!points?.length) {
    return <p className="empty-note">Chưa có dữ liệu lịch sử trong 30 ngày.</p>;
  }
  return (
    <ResponsiveContainer width="100%" height={340}>
      <LineChart data={points} margin={{ top: 8, right: 24, bottom: 8, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="date" tickFormatter={(d) => `${d.slice(8, 10)}/${d.slice(5, 7)}`} />
        <YAxis width={64} domain={['auto', 'auto']} tickFormatter={(v) => `${Math.round(v / 1000)}k`} />
        <Tooltip formatter={(value) => formatVND(value)} labelFormatter={(d) => `Ngày ${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(0, 4)}`} />
        <Legend />
        {series.map((name, index) => (
          <Line key={name} type="monotone" dataKey={name} stroke={COLORS[index % COLORS.length]} strokeWidth={2} dot={{ r: 3 }} connectNulls />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
