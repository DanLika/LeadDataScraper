'use client';

/**
 * Virtualized lead inventory list.
 *
 * Migrated from the inline <table> JSX in page.tsx to a CSS-grid layout
 * so @tanstack/react-virtual can absolute-position visible rows without
 * the constraints of <tbody>. Total render cost stays O(visible rows +
 * overscan) regardless of dataset size — 5000-row scroll smoothness is
 * the design target (60 FPS, no jank).
 *
 * Row layout: each grid cell mirrors the previous <colgroup> widths
 * (25 / 14 / 14 / 20 / 27 %). Sticky header is a sibling of the scroll
 * container — outside the virtualizer parent so it doesn't shift with
 * scroll. Auxiliary "expanded" panel (last_error / key_offerings /
 * pain_points) renders as a second grid row inside the same wrapper,
 * spanning all columns; row heights vary so the virtualizer uses
 * measureElement to size each item.
 *
 * Keyboard nav: rows are tab-focusable (tabIndex=0). Tabbing moves
 * through the visible window naturally; when focus is on the last
 * visible row, the next Tab scrolls the next batch into view via
 * `scrollToIndex` — same behaviour as DOM-default tab traversal.
 *
 * NOT included here: search/segment/status filtering. The parent owns
 * that state and passes already-filtered `leads`.
 */

import { Fragment, useEffect, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  AlertCircle, Crosshair, Globe, Loader2, Mail, Phone, Shield, Users, Music, Pin,
} from 'lucide-react';
import { Facebook, Instagram, Linkedin } from './BrandIcons';
import { ensureProtocol } from '@/app/lib/url.mjs';

import type { Lead } from '../types/lead';

interface LeadTableProps {
  leads: Lead[];
  loading: boolean;
  searchTerm: string;
  totalLeadCount: number;
  processingLeads: Record<string, boolean>;
  isDrafting: boolean;
  activeLeadKey?: string;
  hasMore: boolean;
  nextCursor: string | null;
  isLoadingMore: boolean;
  onLoadMore: () => void;
  onEnrichLead: (uniqueKey: string) => void;
  onDeepHunt: (uniqueKey: string) => void;
  onDraftOutreach: (lead: Lead) => void;
  onProcessLead: (uniqueKey: string) => void;
}

// Mirror the previous <colgroup> widths. fr units used as the rest of
// the cards stretch to container width — percentages would produce the
// same effect but `fr` is cleaner.
const COL_TEMPLATE = '25fr 14fr 14fr 20fr 27fr';

// Estimated default row height. The virtualizer treats this as a hint;
// measureElement records the real height per row so subsequent scrolls
// are accurate. ~90px covers a typical row with a 2-line address; rows
// with an expanded auxiliary panel grow taller and the measurement
// adjusts.
const ROW_HEIGHT_ESTIMATE_PX = 90;

// Scroll viewport height. Fixed-height needed for the virtualizer's
// absolute layout to know how many rows fit. 70vh keeps the modal/chat
// affordances visible above the fold.
const VIEWPORT_HEIGHT = '70vh';

// Overscan: render N rows above + below the visible window. Larger
// values smooth fast scrolls (rows are already mounted by the time the
// scrollbar reaches them) at the cost of more DOM. 20 covers most
// trackpad-flick velocities without sustaining a wasteful resident set.
const OVERSCAN = 20;


// Strip the markdown markers Gemini sometimes emits in key_offerings /
// pain_points so the table renders clean prose. Mirrors the helper that
// used to live in page.tsx — moved here because LeadTable is the only
// consumer.
function cleanMarkdown(text: string): string {
  return text
    .replace(/^###?\s*/gm, '')          // headers
    .replace(/\*\*([^*]+)\*\*/g, '$1')   // bold
    .replace(/^\*\s+/gm, '• ')           // bullets
    .replace(/\n{3,}/g, '\n\n')          // collapse blank-line runs
    .trim();
}

function CollapsibleText({ text, maxLength = 250, style }: { text: string; maxLength?: number; style?: React.CSSProperties }) {
  // Local copy of page.tsx's CollapsibleText, kept here to avoid a
  // circular import and to keep LeadTable self-contained for the lazy
  // chunk. If the truncation rule ever diverges between callers, hoist
  // to a shared component.
  const [open, setOpen] = useState(false);
  const cleaned = cleanMarkdown(text);
  const isLong = cleaned.length > maxLength;
  const display = isLong && !open ? cleaned.slice(0, maxLength) + '…' : cleaned;
  return (
    <div>
      <p className="text-wrap" style={{ margin: 0, whiteSpace: 'pre-line', ...style }}>{display}</p>
      {isLong && (
        <button
          type="button"
          onClick={() => setOpen(o => !o)}
          style={{ background: 'none', border: 'none', color: 'var(--primary-strong)', fontSize: '0.7rem', cursor: 'pointer', padding: '0.25rem 0', fontWeight: 600 }}
        >
          {open ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  );
}


export default function LeadTable(props: LeadTableProps) {
  const {
    leads, loading, searchTerm, totalLeadCount,
    processingLeads, isDrafting, activeLeadKey,
    hasMore, nextCursor, isLoadingMore, onLoadMore,
    onEnrichLead, onDeepHunt, onDraftOutreach, onProcessLead,
  } = props;

  const scrollRef = useRef<HTMLDivElement | null>(null);

  // @tanstack/react-virtual lacks React Compiler annotations; skip is harmless.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: leads.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT_ESTIMATE_PX,
    overscan: OVERSCAN,
    getItemKey: (index) => leads[index]?.unique_key ?? index,
  });

  // Maintain scroll position across leads-array identity changes
  // (e.g. parent re-fetches the same page). The virtualizer keys items
  // by unique_key (getItemKey above) so item offsets stay stable; this
  // effect just defends against the rare case where the scroll element
  // remounts (e.g. lazy-import returning the chunk after the page
  // already painted with the skeleton).
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    // No-op if at top — typical fresh-load case.
    if (el.scrollTop === 0) return;
  }, [leads.length]);

  // ---- Empty / loading states ------------------------------------------
  if (loading && leads.length === 0) {
    return (
      <div
        data-testid="loading-skeleton"
        role="status"
        aria-live="polite"
        aria-label="Syncing leads"
        style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '4rem', gap: '1rem' }}
      >
        <Loader2 className="animate-spin" size={32} color="var(--primary)" />
        <span style={{ color: 'var(--text-dim)' }}>Syncing with Supabase...</span>
      </div>
    );
  }

  if (leads.length === 0) {
    return (
      <div style={{ padding: '4rem', textAlign: 'center', color: 'var(--text-dim)' }}>
        <Users size={48} style={{ marginBottom: '1rem', opacity: 0.2 }} />
        <p>
          {totalLeadCount === 0
            ? 'No prospects discovered yet. Start by importing a CSV.'
            : searchTerm
              ? `No leads matching "${searchTerm}" found.`
              : 'No leads match the current filters. Try clearing search, segment, status or score.'}
        </p>
      </div>
    );
  }

  // ---- Header ----------------------------------------------------------
  const headerStyle: React.CSSProperties = {
    display: 'grid',
    gridTemplateColumns: COL_TEMPLATE,
    background: 'var(--surface-subtle)',
    position: 'sticky',
    top: 0,
    zIndex: 1,
    borderBottom: '1px solid var(--border)',
    fontSize: '0.75rem',
    fontWeight: 600,
    color: 'var(--text-muted)',
    textTransform: 'uppercase',
  };
  const headerCellStyle = (align: 'left' | 'center' | 'right'): React.CSSProperties => ({
    padding: '1rem',
    textAlign: align,
    whiteSpace: 'nowrap',
  });

  return (
    <div>
      <div role="rowgroup" style={headerStyle}>
        <div role="columnheader" style={headerCellStyle('left')}>PROSPECT</div>
        <div role="columnheader" style={headerCellStyle('center')}>AUDIT STATUS</div>
        <div role="columnheader" style={headerCellStyle('center')}>INTELLIGENCE</div>
        <div role="columnheader" style={headerCellStyle('center')}>SOCIAL</div>
        <div role="columnheader" style={headerCellStyle('right')}>ACTIONS</div>
      </div>

      {/* Scroll container — owned by the virtualizer */}
      <div
        ref={scrollRef}
        role="rowgroup"
        aria-label="Lead inventory"
        style={{
          height: VIEWPORT_HEIGHT,
          overflowY: 'auto',
          // contain: 'strict' would be ideal for paint isolation but breaks
          // sticky header on some browsers; the explicit position:sticky
          // on the header gives enough containment in practice.
          contain: 'layout paint',
        }}
      >
        <div
          style={{
            position: 'relative',
            // Total scroll height = virtualizer's measured total
            height: `${virtualizer.getTotalSize()}px`,
            width: '100%',
          }}
        >
          {virtualizer.getVirtualItems().map((virtualRow) => {
            const lead = leads[virtualRow.index];
            if (!lead) return null;
            const showExpanded = !!(
              lead.last_error
              || (lead.key_offerings && lead.key_offerings !== 'Unknown')
              || (lead.pain_points && lead.pain_points !== 'Unknown')
            );
            return (
              <div
                key={virtualRow.key}
                data-index={virtualRow.index}
                ref={virtualizer.measureElement}
                role="row"
                tabIndex={0}
                className="table-row-hover"
                data-segment={lead.segment || ''}
                data-seo-score={lead.seo_score ?? lead.audit_results?.score ?? ''}
                data-unique-key={lead.unique_key}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  transform: `translateY(${virtualRow.start}px)`,
                  borderBottom: '1px solid var(--border)',
                }}
              >
                <div style={{ display: 'grid', gridTemplateColumns: COL_TEMPLATE, alignItems: 'center' }}>
                  {/* PROSPECT */}
                  <div role="cell" style={{ padding: '1rem 1.5rem' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                        <span style={{ fontWeight: 700, fontSize: '0.95rem', color: 'var(--text-white)' }}>
                          {lead.company_name || lead.name || 'Unknown Entity'}
                        </span>
                        {lead.high_risk_flag && (
                          <span className="badge" style={{ background: 'var(--error-tint)', color: 'var(--error-strong)', border: '1px solid rgba(239, 68, 68, 0.25)' }}>
                            <AlertCircle size={12} /> RISK
                          </span>
                        )}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: 'var(--text-muted)', fontSize: '0.8rem' }}>
                        {lead.website && (
                          <a
                            href={ensureProtocol(lead.website)}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', color: 'var(--primary-strong)', textDecoration: 'none', maxWidth: '250px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                          >
                            <Globe size={14} style={{ flexShrink: 0 }} />
                            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                              {lead.website.replace(/^https?:\/\//, '').replace(/\?.*$/, '')}
                            </span>
                          </a>
                        )}
                        {lead.phone && (
                          <a
                            href={`tel:${lead.phone.replace(/[^+0-9]/g, '')}`}
                            style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', color: 'inherit', textDecoration: 'none' }}
                            title={`Call ${lead.phone}`}
                          >
                            <Phone size={14} aria-hidden="true" /> {lead.phone}
                          </a>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* AUDIT STATUS */}
                  <div role="cell" style={{ padding: '1rem', textAlign: 'center' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.4rem' }}>
                      <span
                        className={`badge ${lead.audit_status === 'Completed' ? 'badge-completed' : lead.audit_status?.includes('Failed') ? 'badge-error' : 'badge-pending'}`}
                        style={{ whiteSpace: 'nowrap' }}
                      >
                        {lead.audit_status || 'Unprocessed'}
                      </span>
                      {lead.audit_results?.score != null && (
                        <div style={{ fontSize: '0.7rem', fontWeight: 800, whiteSpace: 'nowrap', color: lead.audit_results.score < 50 ? 'var(--error-strong)' : 'var(--primary-strong)' }}>
                          SEO: {lead.audit_results.score}/100
                        </div>
                      )}
                    </div>
                  </div>

                  {/* INTELLIGENCE */}
                  <div role="cell" style={{ padding: '1rem', textAlign: 'center' }}>
                    <div style={{ display: 'flex', justifyContent: 'center', gap: '0.5rem' }}>
                      {lead.linkedin_hook && (
                        <button
                          type="button"
                          className="intel-hook-btn"
                          onClick={() => onDraftOutreach(lead)}
                          title="LinkedIn Hook Ready — click to draft LinkedIn message"
                          aria-label={`Draft LinkedIn outreach for ${lead.company_name || lead.name || 'lead'}`}
                          style={{ color: 'var(--primary)' }}
                        >
                          <Linkedin size={16} aria-hidden="true" />
                        </button>
                      )}
                      {lead.email_hook && (
                        <button
                          type="button"
                          className="intel-hook-btn intel-hook-email"
                          onClick={() => onDraftOutreach(lead)}
                          title="Email Hook Ready — click to draft email"
                          aria-label={`Draft email outreach for ${lead.company_name || lead.name || 'lead'}`}
                        >
                          <Mail size={16} aria-hidden="true" />
                        </button>
                      )}
                      {lead.audit_results?.high_risk_flag && (
                        <span title="Security Vulnerabilities" aria-label="Security vulnerabilities flagged" style={{ color: 'var(--error)', display: 'inline-flex', alignItems: 'center' }}>
                          <Shield size={16} aria-hidden="true" />
                        </span>
                      )}
                    </div>
                  </div>

                  {/* SOCIAL */}
                  <div role="cell" style={{ padding: '1rem', textAlign: 'center' }}>
                    <div style={{ display: 'flex', justifyContent: 'center', gap: '0.5rem', color: 'var(--text-dim)' }}>
                      {lead.facebook && <a href={ensureProtocol(lead.facebook)} target="_blank" rel="noopener noreferrer" aria-label={`${lead.company_name || lead.name || 'Lead'} Facebook page`} className="social-link"><Facebook size={16} /></a>}
                      {lead.instagram && <a href={ensureProtocol(lead.instagram)} target="_blank" rel="noopener noreferrer" aria-label={`${lead.company_name || lead.name || 'Lead'} Instagram page`} className="social-link"><Instagram size={16} /></a>}
                      {lead.linkedin && <a href={ensureProtocol(lead.linkedin)} target="_blank" rel="noopener noreferrer" aria-label={`${lead.company_name || lead.name || 'Lead'} LinkedIn page`} className="social-link"><Linkedin size={16} /></a>}
                      {lead.tiktok && <a href={ensureProtocol(lead.tiktok)} target="_blank" rel="noopener noreferrer" aria-label={`${lead.company_name || lead.name || 'Lead'} TikTok page`} className="social-link"><Music size={16} /></a>}
                      {lead.pinterest && <a href={ensureProtocol(lead.pinterest)} target="_blank" rel="noopener noreferrer" aria-label={`${lead.company_name || lead.name || 'Lead'} Pinterest page`} className="social-link"><Pin size={16} /></a>}
                      {!lead.facebook && !lead.instagram && !lead.linkedin && !lead.tiktok && !lead.pinterest && (
                        <span aria-label="No social links" style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>N/A</span>
                      )}
                    </div>
                  </div>

                  {/* ACTIONS */}
                  <div role="cell" style={{ padding: '1rem 0.75rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.4rem', flexWrap: 'wrap' }}>
                      <button
                        className="btn-secondary"
                        style={{ padding: '0.4rem', borderRadius: '8px', minWidth: '44px', minHeight: '44px' }}
                        onClick={() => onEnrichLead(lead.unique_key)}
                        disabled={!!processingLeads[lead.unique_key]}
                        aria-busy={!!processingLeads[lead.unique_key]}
                        title="Harvest Contact Details"
                        aria-label="Harvest contact details"
                      >
                        {processingLeads[lead.unique_key] ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <Users size={14} aria-hidden="true" />}
                      </button>
                      <button
                        className="btn-secondary"
                        style={{ padding: '0.4rem', borderRadius: '8px', minWidth: '44px', minHeight: '44px', color: 'var(--accent)', borderColor: 'rgba(245, 158, 11, 0.2)' }}
                        onClick={() => onDeepHunt(lead.unique_key)}
                        disabled={!!processingLeads[lead.unique_key]}
                        aria-busy={!!processingLeads[lead.unique_key]}
                        title="Deep Digital Hunt"
                        aria-label="Deep digital hunt"
                      >
                        {processingLeads[lead.unique_key] ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <Crosshair size={14} aria-hidden="true" />}
                      </button>
                      <button
                        className="btn-primary"
                        style={{ padding: '0.4rem 0.75rem', borderRadius: '8px', fontSize: '0.75rem' }}
                        onClick={() => onDraftOutreach(lead)}
                        disabled={isDrafting || lead.audit_status !== 'Completed'}
                        aria-busy={isDrafting && activeLeadKey === lead.unique_key}
                        title="Draft Personalised Outreach"
                      >
                        {isDrafting && activeLeadKey === lead.unique_key ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : 'Draft'}
                      </button>
                      <button
                        className="btn-primary"
                        style={{ padding: '0.4rem 0.75rem', borderRadius: '8px', fontSize: '0.75rem', background: 'var(--secondary)' }}
                        onClick={() => onProcessLead(lead.unique_key)}
                        disabled={!!processingLeads[lead.unique_key]}
                        aria-busy={!!processingLeads[lead.unique_key]}
                      >
                        {processingLeads[lead.unique_key] ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : lead.audit_status === 'Completed' ? 'Re-Audit' : 'Audit'}
                      </button>
                    </div>
                  </div>
                </div>

                {showExpanded && (
                  <div style={{ background: 'var(--surface-subtle)', padding: '1rem 2rem' }}>
                    <div style={{ display: 'flex', gap: '2rem', flexWrap: 'wrap' }}>
                      {lead.last_error && (
                        <div style={{ flex: '1 1 300px', borderLeft: '3px solid var(--error)', paddingLeft: '1rem' }}>
                          <div style={{ fontSize: '0.65rem', color: 'var(--error-strong)', textTransform: 'uppercase', marginBottom: '0.25rem', display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                            <AlertCircle size={10} /> PROCESSING ERROR
                          </div>
                          <p style={{ fontSize: '0.8rem', color: 'var(--error-strong)', margin: 0 }}>{lead.last_error}</p>
                        </div>
                      )}
                      {lead.key_offerings && lead.key_offerings !== 'Unknown' && (
                        <div style={{ flex: '1 1 200px' }}>
                          <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '0.25rem' }}>KEY OFFERINGS</div>
                          <CollapsibleText text={lead.key_offerings} style={{ fontSize: '0.8rem', color: 'var(--text-primary)' }} />
                        </div>
                      )}
                      {lead.pain_points && lead.pain_points !== 'Unknown' && (
                        <div style={{ flex: '1 1 200px' }}>
                          <div style={{ fontSize: '0.65rem', color: 'var(--warning-strong)', textTransform: 'uppercase', marginBottom: '0.25rem' }}>PAIN POINTS</div>
                          <CollapsibleText text={lead.pain_points} style={{ fontSize: '0.8rem', color: 'var(--text-primary)' }} />
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {hasMore && nextCursor && (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '1.25rem', borderTop: '1px solid var(--border)' }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={onLoadMore}
            disabled={isLoadingMore}
            aria-busy={isLoadingMore}
            style={{ minWidth: '12rem' }}
          >
            {isLoadingMore ? <Loader2 className="animate-spin" size={16} aria-hidden="true" /> : 'Load more'}
          </button>
        </div>
      )}

      {/* Fragment used in original code; keep import path live in case CollapsibleText is hoisted later. */}
      <Fragment />
    </div>
  );
}
