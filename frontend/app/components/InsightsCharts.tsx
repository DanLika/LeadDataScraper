'use client';

import {
  Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip,
  ResponsiveContainer, PieChart, Pie, Legend, BarChart,
} from 'recharts';

interface Stats {
  total_leads: number;
  audit_status_distribution: Array<{ name: string; value: number }>;
  seo_score_ranges: Array<{ range: string; count: number }>;
  source_distribution: Array<{ name: string; value: number }>;
}

interface InsightsChartsProps {
  stats: Stats | null;
  colors: string[];
}

export default function InsightsCharts({ stats, colors }: InsightsChartsProps) {
  return (
    <div className="grid-responsive-2" style={{ marginBottom: '2rem' }}>
      <div className="card">
        <h2 className="card-title" style={{ marginBottom: '1.5rem' }}>Audit Status Breakdown</h2>
        <div
          style={{ width: '100%' }}
          role="img"
          aria-label={
            stats?.audit_status_distribution?.length
              ? `Audit status distribution: ${stats.audit_status_distribution.map(d => `${d.value} ${d.name}`).join(', ')}.`
              : 'Audit status distribution chart — no data yet'
          }
        >
          <ResponsiveContainer width="100%" height={300}>
            <PieChart>
              <Pie
                data={(stats?.audit_status_distribution || []).map((d, i) => ({ ...d, fill: colors[i % colors.length] }))}
                cx="50%"
                cy="50%"
                innerRadius={60}
                outerRadius={100}
                paddingAngle={5}
                dataKey="value"
              />
              <RechartsTooltip
                contentStyle={{ background: 'var(--surface-tooltip)', border: '1px solid var(--border-tooltip)', borderRadius: '8px' }}
              />
              <Legend verticalAlign="bottom" height={36} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="card">
        <h2 className="card-title" style={{ marginBottom: '1.5rem' }}>SEO Score Distribution</h2>
        <div
          style={{ width: '100%' }}
          role="img"
          aria-label={
            stats?.seo_score_ranges?.length
              ? `SEO score distribution: ${stats.seo_score_ranges.map(r => `${r.count} leads in ${r.range}`).join(', ')}.`
              : 'SEO score distribution chart — no data yet'
          }
        >
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={stats?.seo_score_ranges || []}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--surface-muted)" vertical={false} />
              <XAxis
                dataKey="range"
                axisLine={false}
                tickLine={false}
                tick={{ fill: 'var(--text-muted)', fontSize: 12 }}
              />
              <YAxis
                axisLine={false}
                tickLine={false}
                tick={{ fill: 'var(--text-muted)', fontSize: 12 }}
              />
              <RechartsTooltip
                cursor={{ fill: 'var(--surface-muted)' }}
                contentStyle={{ background: 'var(--surface-tooltip)', border: '1px solid var(--border-tooltip)', borderRadius: '8px' }}
              />
              <Bar dataKey="count" fill="var(--primary)" radius={[4, 4, 0, 0]} barSize={40} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
