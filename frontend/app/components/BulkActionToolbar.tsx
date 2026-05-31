'use client';

import { Download, Loader2, Trash2, Zap } from 'lucide-react';

interface BulkActionToolbarProps {
  count: number;
  isAuditing: boolean;
  onRunAudit: () => void;
  onExportSelected: () => void;
  onClear: () => void;
}

/**
 * Floating bar shown above the LeadTable when at least one lead is
 * selected. Three actions:
 *   - Run audit  → POST /audit-batch (caps at 200 server-side; UI
 *                  trusts that cap but mirrors a confirm() in the
 *                  parent handler that names count + cost).
 *   - Export CSV → client-side blob from currently-selected rows.
 *   - Clear      → drops selection back to zero.
 *
 * Rendering rule lives in the caller — when `count === 0` the parent
 * skips this component entirely. We still defensively return `null` so
 * a misuse doesn't blast an empty toolbar across the layout.
 */
export default function BulkActionToolbar({
  count,
  isAuditing,
  onRunAudit,
  onExportSelected,
  onClear,
}: BulkActionToolbarProps) {
  if (count === 0) return null;

  return (
    <div
      role="region"
      aria-label="Bulk actions"
      data-testid="bulk-action-toolbar"
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '1rem',
        flexWrap: 'wrap',
        padding: '0.75rem 1rem',
        marginBottom: '0.75rem',
        background: 'var(--surface-elevated)',
        border: '1px solid var(--primary-tint, var(--border))',
        borderRadius: '10px',
        boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
      }}
    >
      <div
        aria-live="polite"
        style={{
          fontSize: '0.85rem',
          fontWeight: 600,
          color: 'var(--text-white)',
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
        }}
      >
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            minWidth: '1.75rem',
            padding: '0.15rem 0.5rem',
            borderRadius: '999px',
            background: 'var(--primary)',
            color: 'var(--text-on-primary, white)',
            fontSize: '0.75rem',
          }}
        >
          {count}
        </span>
        <span>Selected</span>
      </div>

      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        <button
          type="button"
          className="btn-primary"
          onClick={onRunAudit}
          disabled={isAuditing}
          aria-busy={isAuditing}
          style={{ padding: '0.45rem 0.85rem', borderRadius: '8px', fontSize: '0.8rem', display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}
          title={`Run audit on ${count} selected lead${count === 1 ? '' : 's'}`}
        >
          {isAuditing ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <Zap size={14} aria-hidden="true" />}
          Run audit
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={onExportSelected}
          style={{ padding: '0.45rem 0.85rem', borderRadius: '8px', fontSize: '0.8rem', display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}
          title="Download a CSV of the selected leads"
        >
          <Download size={14} aria-hidden="true" />
          Export selected
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={onClear}
          style={{ padding: '0.45rem 0.85rem', borderRadius: '8px', fontSize: '0.8rem', display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}
          title="Deselect all"
        >
          <Trash2 size={14} aria-hidden="true" />
          Clear selection
        </button>
      </div>
    </div>
  );
}
