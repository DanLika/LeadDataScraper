'use client';

import { Search } from 'lucide-react';

interface FilterBarProps {
  searchTerm: string;
  setSearchTerm: (value: string) => void;
  filterSegment: string;
  setFilterSegment: (value: string) => void;
  filterAuditStatus: string;
  setFilterAuditStatus: (value: string) => void;
  filterMinScore: number;
  setFilterMinScore: (value: number) => void;
  segmentOptions: (string | undefined)[];
}

export default function FilterBar({
  searchTerm,
  setSearchTerm,
  filterSegment,
  setFilterSegment,
  filterAuditStatus,
  setFilterAuditStatus,
  filterMinScore,
  setFilterMinScore,
  segmentOptions,
}: FilterBarProps) {
  return (
    <div style={{ padding: '1.5rem 2rem', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'var(--surface-muted)', flexWrap: 'wrap', gap: '1.5rem', minWidth: 'min-content' }}>
      <h3 style={{ fontSize: '1.25rem', fontWeight: 700 }}>Prospect Inventory</h3>
      <div className="filters-row" style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: '1', minWidth: '200px' }}>
          <Search size={18} style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-dim)' }} />
          <input
            type="text"
            placeholder="Search leads..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            id="search-leads"
            aria-label="Search leads"
            style={{ background: 'var(--surface-muted)', border: '1px solid var(--glass-border)', borderRadius: '12px', padding: '0.6rem 1rem 0.6rem 2.75rem', color: 'var(--text-white)', width: '100%', fontSize: '0.9rem', outline: 'none' }}
          />
        </div>
        <select
          value={filterSegment}
          onChange={(e) => setFilterSegment(e.target.value)}
          id="filter-segment"
          aria-label="Filter by segment"
          style={{ background: 'var(--surface-muted)', border: '1px solid var(--glass-border)', borderRadius: '12px', padding: '0.6rem 1rem', color: 'var(--text-white)', fontSize: '0.9rem', outline: 'none' }}
        >
          <option value="all" style={{ background: 'var(--surface-dark)' }}>All Segments</option>
          {segmentOptions.map((seg) => (
            <option key={seg} value={seg} style={{ background: 'var(--surface-dark)' }}>{seg}</option>
          ))}
        </select>

        <select
          value={filterAuditStatus}
          onChange={(e) => setFilterAuditStatus(e.target.value)}
          id="filter-audit-status"
          aria-label="Filter by audit status"
          style={{ background: 'var(--surface-muted)', border: '1px solid var(--glass-border)', borderRadius: '12px', padding: '0.6rem 1rem', color: 'var(--text-white)', fontSize: '0.9rem', outline: 'none' }}
        >
          <option value="all" style={{ background: 'var(--surface-dark)' }}>All Statuses</option>
          <option value="Completed" style={{ background: 'var(--surface-dark)' }}>Completed</option>
          <option value="Pending" style={{ background: 'var(--surface-dark)' }}>Pending</option>
          <option value="Failed" style={{ background: 'var(--surface-dark)' }}>Failed</option>
        </select>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: 'var(--surface-muted)', border: '1px solid var(--glass-border)', borderRadius: '12px', padding: '0.6rem 1rem' }}>
          {/* Filters on outreach_score (not seo_score). Earlier wording
              ("Score: 0+") was ambiguous because both scores are visible
              elsewhere in the inventory and the Insights page. */}
          <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Outreach: {filterMinScore}+</span>
          <input
            type="range"
            min="0"
            max="100"
            value={filterMinScore}
            onChange={(e) => setFilterMinScore(parseInt(e.target.value))}
            id="filter-min-score"
            aria-label="Minimum outreach score"
            style={{ accentColor: 'var(--primary)', width: '100px' }}
          />
        </div>
      </div>
    </div>
  );
}
