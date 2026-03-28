'use client';

import { useMemo } from 'react';
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from 'recharts';
import { Shield } from 'lucide-react';

interface Lead {
  id?: string;
  unique_key: string;
  company_name?: string;
  name?: string;
  website?: string;
  audit_status?: string;
  retry_count: number;
  audit_results?: {
    score: number;
    high_risk_flag?: boolean;
  };
  high_risk_flag?: boolean;
  email?: string;
  segment?: string;
}

interface HealthChartProps {
  leads: Lead[];
}

export default function HealthChart({ leads }: HealthChartProps) {
  const healthData = useMemo(() => {
    const highRisk = leads.filter((l) => (!!l.audit_results && l.audit_results.score < 50) || l.high_risk_flag || l.audit_results?.high_risk_flag).length;
    const healthy = leads.filter((l) => l.audit_status === 'Completed' && !!l.audit_results && l.audit_results.score >= 50 && !l.high_risk_flag && !l.audit_results?.high_risk_flag).length;
    const pending = leads.filter((l) => l.audit_status === 'Pending' || !l.audit_status).length;

    return [
      { name: 'Healthy', value: healthy, color: 'var(--success-light)' },
      { name: 'High Risk', value: highRisk, color: 'var(--error)' },
      { name: 'Pending', value: pending, color: 'var(--warning)' },
    ];
  }, [leads]);

  return (
    <section style={{ marginBottom: '3.5rem' }}>
      <div className="card card-no-hover" style={{ padding: '1.5rem 2rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
          <div>
            <h3 style={{ fontSize: '1.25rem', fontWeight: 700 }}>Lead Health Analysis</h3>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>Visual breakdown of your lead database status.</p>
          </div>
        </div>
        <div className="grid-responsive-health">
          <div style={{ height: '240px', width: '100%' }} role="img" aria-label="Lead health breakdown chart">
            <ResponsiveContainer width="100%" height="100%" minHeight={200}>
              <PieChart>
                <Pie
                  data={healthData}
                  cx="50%"
                  cy="50%"
                  innerRadius={60}
                  outerRadius={80}
                  paddingAngle={5}
                  dataKey="value"
                >
                  {healthData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ background: 'var(--surface-tooltip)', border: '1px solid var(--border-tooltip)', borderRadius: '8px', color: 'var(--text-heading)' }}
                  itemStyle={{ color: 'var(--text-heading)' }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: '1rem' }}>
             <div className="grid-health-stats">
                {healthData.map((item, idx) => (
                  <div key={idx} style={{ padding: '1rem', background: 'var(--surface-muted)', borderRadius: '12px', border: '1px solid var(--border)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
                      <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: item.color }} />
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 600, textTransform: 'uppercase' }}>{item.name}</span>
                    </div>
                    <div style={{ fontSize: '1.5rem', fontWeight: 800 }}>{item.value}</div>
                  </div>
                ))}
             </div>
             <div style={{ padding: '1.25rem', background: 'var(--primary-tint-10)', borderRadius: '12px', border: '1px solid var(--primary-tint-10)' }}>
               <p style={{ margin: 0, fontSize: '0.875rem', color: 'var(--primary-light)', display: 'flex', alignItems: 'flex-start', gap: '0.5rem' }}>
                 <Shield size={16} style={{ flexShrink: 0, marginTop: '2px' }} />
                 <span><strong>Analytics Insight:</strong> {
                   leads.length > 0 ?
                   `${Math.round((leads.filter(l => l.email).length / leads.length) * 100)}% of your leads have verified emails.` :
                   "Import leads to see deep health analytics."
                 }</span>
               </p>
             </div>
          </div>
        </div>
      </div>
    </section>
  );
}
