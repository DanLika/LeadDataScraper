'use client';

import { Search, X, Sparkles } from 'lucide-react';

export type SortKey =
  | 'created_at_desc'
  | 'seo_score_desc'
  | 'seo_score_asc'
  | 'outreach_score_desc'
  | 'name_asc'
  | 'name_desc';

export const DEFAULT_SORT: SortKey = 'created_at_desc';

interface FilterBarProps {
  searchTerm: string;
  setSearchTerm: (value: string) => void;
  filterSegment: string;
  setFilterSegment: (value: string) => void;
  filterAuditStatus: string;
  setFilterAuditStatus: (value: string) => void;
  filterMinScore: number;
  setFilterMinScore: (value: number) => void;
  sortKey: SortKey;
  setSortKey: (value: SortKey) => void;
  segmentOptions: (string | undefined)[];
  onClearFilters: () => void;
  hasActiveFilters: boolean;
  /** Phase 13.3 — when true, the dashboard requests `?include_demo=true`
   *  and the seeded `_demo_*` rows show alongside real leads. Default
   *  false (operator-facing dashboards hide demo data so screenshots /
   *  walkthrough videos can flip the toggle on for a moment). */
  showDemo: boolean;
  setShowDemo: (value: boolean) => void;
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
  sortKey,
  setSortKey,
  segmentOptions,
  onClearFilters,
  hasActiveFilters,
  showDemo,
  setShowDemo,
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

        <select
          value={sortKey}
          onChange={(e) => setSortKey(e.target.value as SortKey)}
          id="sort-leads"
          aria-label="Sort leads"
          style={{ background: 'var(--surface-muted)', border: '1px solid var(--glass-border)', borderRadius: '12px', padding: '0.6rem 1rem', color: 'var(--text-white)', fontSize: '0.9rem', outline: 'none' }}
        >
          <option value="created_at_desc" style={{ background: 'var(--surface-dark)' }}>Newest first</option>
          <option value="seo_score_desc" style={{ background: 'var(--surface-dark)' }}>SEO score: high → low</option>
          <option value="seo_score_asc" style={{ background: 'var(--surface-dark)' }}>SEO score: low → high</option>
          <option value="outreach_score_desc" style={{ background: 'var(--surface-dark)' }}>Outreach score: high → low</option>
          <option value="name_asc" style={{ background: 'var(--surface-dark)' }}>Name A→Z</option>
          <option value="name_desc" style={{ background: 'var(--surface-dark)' }}>Name Z→A</option>
        </select>

        <button
          type="button"
          role="switch"
          aria-checked={showDemo}
          id="toggle-show-demo"
          onClick={() => setShowDemo(!showDemo)}
          title="Show Phase 13.3 demo seed data alongside real leads"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.4rem',
            background: showDemo ? 'var(--primary-tint-15)' : 'transparent',
            border: `1px solid ${showDemo ? 'var(--primary)' : 'var(--border)'}`,
            color: showDemo ? 'var(--primary)' : 'var(--text-primary)',
            borderRadius: '12px',
            padding: '0.55rem 0.85rem',
            fontSize: '0.85rem',
            cursor: 'pointer',
            minHeight: '44px',
          }}
        >
          <Sparkles size={14} aria-hidden="true" /> {showDemo ? 'Hide demo data' : 'Show demo data'}
        </button>

        {hasActiveFilters && (
          <button
            type="button"
            onClick={onClearFilters}
            id="clear-filters"
            aria-label="Clear filters"
            style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-primary)', borderRadius: '12px', padding: '0.55rem 0.85rem', fontSize: '0.85rem', cursor: 'pointer', minHeight: '44px' }}
          >
            <X size={14} aria-hidden="true" /> Clear filters
          </button>
        )}
      </div>
    </div>
  );
}
