'use client';

import { BarChart3, Shield, AlertTriangle, CheckCircle } from 'lucide-react';

interface Lead {
  id?: string;
  unique_key: string;
  audit_status?: string;
  retry_count: number;
  audit_results?: {
    score: number;
    high_risk_flag?: boolean;
  };
  high_risk_flag?: boolean;
}

interface StatsCardsProps {
  leads: Lead[];
}

export default function StatsCards({ leads }: StatsCardsProps) {
  return (
    <section className="grid-responsive-stats" style={{ marginBottom: '3.5rem' }}>
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--text-muted)', marginBottom: '1rem' }}>
           <span style={{ fontSize: '0.875rem', fontWeight: 600 }}>TOTAL LEADS</span>
           <BarChart3 size={18} />
        </div>
        <div style={{ fontSize: '2.5rem', fontWeight: 800 }}>{leads.length}</div>
      </div>
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--primary-strong)', marginBottom: '1rem' }}>
           <span style={{ fontSize: '0.875rem', fontWeight: 600 }}>PENDING</span>
           <Shield size={18} />
        </div>
        <div style={{ fontSize: '2.5rem', fontWeight: 800 }}>{leads.filter((l) => l.audit_status === 'Pending').length}</div>
      </div>
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--error-strong)', marginBottom: '1rem' }}>
           <span style={{ fontSize: '0.875rem', fontWeight: 600 }}>HIGH RISK</span>
           <AlertTriangle size={18} />
        </div>
        <div style={{ fontSize: '2.5rem', fontWeight: 800 }}>
          {leads.filter((l) => (l.audit_results?.score ?? 100) < 50 || l.high_risk_flag || l.audit_results?.high_risk_flag).length}
        </div>
      </div>
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--success-strong)', marginBottom: '1rem' }}>
           <span style={{ fontSize: '0.875rem', fontWeight: 600 }}>HEALTHY</span>
           <CheckCircle size={18} />
        </div>
        <div style={{ fontSize: '2.5rem', fontWeight: 800 }}>
          {leads.filter((l) =>
            l.audit_status === 'Completed'
            && !!l.audit_results
            && (l.audit_results.score ?? 0) >= 50
            && !l.high_risk_flag
            && !l.audit_results?.high_risk_flag
          ).length}
        </div>
      </div>
    </section>
  );
}
