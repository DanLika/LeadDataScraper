'use client';

import { useState } from 'react';
import { Download, FileSpreadsheet, AlertTriangle, Star, CheckCircle, Loader2, Users } from 'lucide-react';
import { API_BASE_URL, apiFetch } from '../utils/apiConfig';

export default function ExportButtons() {
  const [exporting, setExporting] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const triggerExport = async () => {
    setExporting(true);
    setStatus(null);
    try {
      const resp = await apiFetch(`${API_BASE_URL}/export`);
      const data = await resp.json();
      if (data.message) {
        setStatus('Success! Check the /exports folder.');
      } else {
        setStatus('Error: ' + data.error);
      }
    } catch (err) {
      console.error('Export failed:', err);
      setStatus('Failed to connect to backend.');
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="glass-card" style={{ padding: '1.5rem', marginBottom: '2rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Download size={20} className="text-primary" />
          Automated Lead Exports
        </h3>
        {status && (
          <span style={{ fontSize: '0.875rem', color: status.includes('Success') ? 'var(--success)' : 'var(--error)', display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
            {status.includes('Success') ? <CheckCircle size={14} /> : <AlertTriangle size={14} />}
            {status}
          </span>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem' }}>
        <button 
          onClick={triggerExport} 
          disabled={exporting}
          className="btn-secondary"
          style={{ justifyContent: 'center', gap: '0.75rem', position: 'relative' }}
        >
          {exporting ? <Loader2 size={18} className="animate-spin" /> : <FileSpreadsheet size={18} />}
          Generate All CSVs
        </button>
        
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', opacity: 0.7, fontSize: '0.8rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <CheckCircle size={12} className="text-secondary" /> full_leads_all_data.csv
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <AlertTriangle size={12} style={{ color: 'var(--warning-light)' }} /> leads_vulnerable.csv
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Star size={12} style={{ color: 'var(--warning)' }} /> high_priority_outreach.csv
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Users size={12} className="text-accent" /> outreach_ready_leads.csv
          </div>
        </div>
      </div>
    </div>
  );
}
