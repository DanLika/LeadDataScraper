'use client';

import { useCallback, useState, useEffect } from 'react';
import {
  TrendingUp, Shield, Users, Target, Zap, ArrowLeft, Loader2, Menu
} from 'lucide-react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import Sidebar from '../components/Sidebar';
import { API_BASE_URL, apiFetch } from '@/utils/apiConfig';
import { useEscape, restoreFocus, BURGER_SELECTOR } from '@/utils/useEscape';

// Recharts is ~80kb gzipped. Defer it to a separate chunk so the
// /insights route shell hits the wire fast; charts render after the
// hydration tick. Same trick for the AI chat island.
const InsightsCharts = dynamic(() => import('../components/InsightsCharts'), {
  ssr: false,
  loading: () => <div className="grid-responsive-2" style={{ marginBottom: '2rem', minHeight: 340 }} aria-hidden="true" />,
});
const AIChat = dynamic(() => import('../components/AIChat'), { ssr: false });

interface Stats {
  total_leads: number;
  audit_status_distribution: Array<{ name: string, value: number }>;
  seo_score_ranges: Array<{ range: string, count: number }>;
  source_distribution: Array<{ name: string, value: number }>;
}

interface Insights {
  summary: string;
  insights: string[];
  top_priorities: Array<{ name: string; reason: string }>;
}

export default function InsightsPage() {
  const router = useRouter();
  const [stats, setStats] = useState<Stats | null>(null);
  const [insights, setInsights] = useState<Insights | null>(null);
  const [leads, setLeads] = useState<Array<{ outreach_score?: number | null; high_risk_flag?: boolean; audit_results?: { score?: number; high_risk_flag?: boolean } | null }>>([]);
  const [loading, setLoading] = useState(true);
  const [fetchingInsights, setFetchingInsights] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  useEscape(() => {
    setIsSidebarOpen(false);
    restoreFocus(BURGER_SELECTOR);
  }, isSidebarOpen);
  const COLORS = ['var(--primary)', 'var(--success)', 'var(--warning)', 'var(--error)', 'var(--secondary)'];

  // Phase 13.3 — honour the dashboard's "Show demo data" toggle. Demo
  // rows are excluded by default; flipping the toggle on the dashboard
  // is reflected here on the next mount (no cross-tab live sync). State
  // (not inline `typeof window` check on every render) keeps the
  // `useCallback` identities stable so the init effect only fires once.
  const [includeDemo, setIncludeDemo] = useState(false);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (window.localStorage.getItem('lds-include-demo') === '1') setIncludeDemo(true);
  }, []);
  const demoSuffix = includeDemo ? '&include_demo=true' : '';

  const fetchLeads = useCallback(async (signal?: AbortSignal) => {
    try {
      // Insights needs the lead set for client-side aggregations (high-risk
      // count, outreach-score buckets). Request the max page size so the
      // counts match what the dashboard shows. Beyond 200 leads we should
      // fold the aggregation into a dedicated /stats-style endpoint
      // rather than paginate-all on the client.
      const response = await apiFetch(`${API_BASE_URL}/leads?limit=200${demoSuffix}`, { signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setLeads(data.leads || []);
    } catch (err) {
      // Benign cancellation (effect cleanup / navigation). WebKit reports
      // it as `TypeError: Load failed`; discriminate on `signal.aborted`.
      if (signal?.aborted) return;
      console.error('Error fetching leads:', err);
    }
  }, [demoSuffix]);

  const fetchStats = useCallback(async (signal?: AbortSignal) => {
    try {
      // `?` then `&` because the suffix starts with `&`. /stats has no
      // other required params so the `?` lives in the join below.
      const url = demoSuffix ? `${API_BASE_URL}/stats?include_demo=true` : `${API_BASE_URL}/stats`;
      const response = await apiFetch(url, { signal });
      const data = await response.json();
      setStats(data);
    } catch (err) {
      if (signal?.aborted) return;
      console.error('Stats fetch failed:', err);
    }
  }, [demoSuffix]);

  const fetchInsightsData = useCallback(async (signal?: AbortSignal) => {
    setFetchingInsights(true);
    try {
      const response = await apiFetch(`${API_BASE_URL}/insights`, { signal });
      const data = await response.json();
      setInsights(data);
    } catch (err) {
      if (signal?.aborted) return;
      console.error('Insights fetch failed:', err);
    } finally {
      if (!signal?.aborted) setFetchingInsights(false);
    }
  }, []);

  useEffect(() => {
    // AbortController scopes the fetches to this effect — React 19
    // StrictMode double-invokes in dev, and without the abort the first
    // run's requests dangle and WebKit logs `TypeError: Load failed`.
    const controller = new AbortController();
    const init = async () => {
      await Promise.all([
        fetchLeads(controller.signal),
        fetchStats(controller.signal),
        fetchInsightsData(controller.signal),
      ]);
      if (!controller.signal.aborted) setLoading(false);
    }
    init();
    return () => controller.abort();
  }, [fetchLeads, fetchStats, fetchInsightsData]);

  if (loading) {
    return (
      <main aria-busy="true" aria-label="Loading Strategic Insights" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', background: 'var(--background)' }}>
        <Loader2 className="animate-spin" size={48} style={{ color: 'var(--primary)' }} aria-hidden="true" />
      </main>
    );
  }

  return (
    <div className="dashboard-container">
      <a href="#main-content" className="skip-link">Skip to main content</a>
      <Sidebar
        view="all"
        setView={(v) => { if (v !== 'all') router.push(`/?view=${v}`); }}
        showDiscoveryModal={false}
        setShowDiscoveryModal={(open) => { if (open) router.push('/?openDiscovery=1'); }}
        showSettings={false}
        setShowSettings={(open) => { if (open) router.push('/?openSettings=1'); }}
        leads={leads}
        fetchingInsights={fetchingInsights}
        insights={insights}
        fetchInsights={fetchInsightsData}
        setSearchTerm={() => {}}
        isOpenMobile={isSidebarOpen}
        setIsOpenMobile={setIsSidebarOpen}
        onCollapsedChange={setIsSidebarCollapsed}
      />

      <main id="main-content" tabIndex={-1} className="main-content" style={{ padding: 0, display: 'flex', flexDirection: 'column', outline: 'none' }}>
        <div className="mobile-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <div className="logo-icon" style={{ width: '32px', height: '32px', borderRadius: '8px' }}>
              <Shield size={18} color="white" />
            </div>
            <strong style={{ fontSize: '1rem', letterSpacing: '-0.02em' }}>LeadScout</strong>
          </div>
          <button
            onClick={() => setIsSidebarOpen(true)}
            style={{ background: 'var(--surface-muted)', border: '1px solid var(--border-subtle)', borderRadius: '10px', padding: '0.5rem', cursor: 'pointer', color: 'var(--text-primary)', display: 'flex', alignItems: 'center', justifyContent: 'center', minWidth: '44px', minHeight: '44px' }}
            aria-label="Open menu"
          >
            <Menu size={22} />
          </button>
        </div>

        <header className="main-header" style={{ padding: '2rem' }}>
          <div className="header-left">
            <Link href="/" className="back-link" aria-label="Back to dashboard" style={{ marginRight: '1rem', color: 'var(--text-muted)' }}>
              <ArrowLeft size={20} />
            </Link>
            <div>
              <h1 className="header-title">Strategic Insights</h1>
              <p className="header-subtitle">Deep analytics and AI-driven growth patterns.</p>
            </div>
          </div>
        </header>

        <div className="content-scroll main-content-wrapper" style={{ padding: '0 2rem 120px 2rem' }}>
          {/* Quick Metrics */}
          <section className="grid-responsive-stats" style={{ marginBottom: '2rem' }}>
            <Link href="/" className="card stat-card" style={{ textDecoration: 'none', color: 'inherit', cursor: 'pointer', display: 'flex', gap: '0.75rem', alignItems: 'center' }} title="Open dashboard">
              <div className="stat-icon" style={{ background: 'var(--primary-tint-10)', color: 'var(--primary)' }}>
                <Users size={20} aria-hidden="true" />
              </div>
              <div>
                <p className="stat-label">Total Leads</p>
                <div className="stat-value">{stats?.total_leads || 0}</div>
              </div>
            </Link>
            <Link href="/?view=audited" className="card stat-card" style={{ textDecoration: 'none', color: 'inherit', cursor: 'pointer', display: 'flex', gap: '0.75rem', alignItems: 'center' }} title="Filter dashboard to audited leads">
              <div className="stat-icon" style={{ background: 'var(--success-tint)', color: 'var(--success)' }}>
                <Shield size={20} aria-hidden="true" />
              </div>
              <div>
                <p className="stat-label">Audited Leads</p>
                <div className="stat-value">
                  {stats?.audit_status_distribution?.find(s => s.name?.toLowerCase() === 'completed')?.value || 0}
                </div>
              </div>
            </Link>
            <div className="card stat-card" title="Leads with SEO score above 70">
              <div className="stat-icon" style={{ background: 'var(--warning-tint)', color: 'var(--warning)' }}>
                <Target size={20} aria-hidden="true" />
              </div>
              <div>
                <p className="stat-label">Top Prospects</p>
                <div className="stat-value">
                  {leads.filter(l => ((l.outreach_score ?? l.audit_results?.score ?? 0)) > 70).length}
                </div>
              </div>
            </div>
            <Link href="/?view=high-risk" className="card stat-card" style={{ textDecoration: 'none', color: 'inherit', cursor: 'pointer', display: 'flex', gap: '0.75rem', alignItems: 'center' }} title="Filter dashboard to high-risk leads">
              <div className="stat-icon" style={{ background: 'var(--error-tint)', color: 'var(--error)' }}>
                <Zap size={20} aria-hidden="true" />
              </div>
              <div>
                <p className="stat-label">High Risk</p>
                <div className="stat-value">
                  {leads.filter(l => l.high_risk_flag).length}
                </div>
              </div>
            </Link>
          </section>

          {/* Charts Row 1 — lazy-loaded (recharts is a ~80kb gzipped chunk) */}
          <InsightsCharts stats={stats} colors={COLORS} />

          {/* Strategic Analysis Section */}
          <section className="card" style={{ marginBottom: '2rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.5rem' }}>
              <div className="stat-icon" style={{ background: 'var(--primary-tint-10)', color: 'var(--primary)', width: '32px', height: '32px' }}>
                <TrendingUp size={18} />
              </div>
              <h2 className="card-title" style={{ marginBottom: 0 }}>AI Strategic Analysis</h2>
            </div>
            
            {fetchingInsights ? (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '3rem 0', gap: '1rem' }}>
                <Loader2 className="animate-spin" size={32} style={{ color: 'var(--primary)' }} />
                <p style={{ color: 'var(--text-muted)' }}>Analyzing market patterns...</p>
              </div>
            ) : insights ? (
              <div className="insights-full-view">
                <div className="summary-banner" style={{ padding: '1.5rem', background: 'var(--primary-tint-5)', borderRadius: '12px', border: '1px solid var(--primary-tint-10)', marginBottom: '2rem' }}>
                  <p style={{ fontSize: '1.125rem', color: 'var(--primary-strong)', lineHeight: 1.6, margin: 0 }}>
                    {insights.summary}
                  </p>
                </div>

                <div className="grid-responsive-2">
                  <div>
                    <h3 style={{ fontSize: '1rem', fontWeight: 700, marginBottom: '1rem', color: 'var(--text-heading)' }}>Key Market Patterns</h3>
                    <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                      {(insights?.insights || []).map((insight, idx) => (
                        <li key={idx} style={{ display: 'flex', gap: '0.75rem', padding: '1rem', background: 'var(--surface-muted)', borderRadius: '12px', border: '1px solid var(--border-subtle)' }}>
                          <span style={{ flexShrink: 0, width: '1.5rem', height: '1.5rem', borderRadius: '50%', background: 'var(--primary-tint-20)', color: 'var(--primary-medium)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.75rem', fontWeight: 700 }}>
                            {idx + 1}
                          </span>
                          <span style={{ color: 'var(--text-secondary)' }}>{insight}</span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div>
                    <h3 style={{ fontSize: '1rem', fontWeight: 700, marginBottom: '1rem', color: 'var(--text-heading)' }}>High-Impact Priorities</h3>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                      {(insights?.top_priorities || []).map((priority, idx) => (
                        <div
                          key={idx}
                          role="button"
                          tabIndex={0}
                          title={`Search dashboard for ${priority.name}`}
                          onClick={() => router.push(`/?search=${encodeURIComponent(priority.name)}`)}
                          onKeyDown={(e: React.KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); router.push(`/?search=${encodeURIComponent(priority.name)}`); } }}
                          style={{ padding: '1rem', background: 'var(--surface-muted)', borderRadius: '12px', border: '1px solid var(--border-subtle)', transition: 'border-color 0.2s, transform 0.1s', cursor: 'pointer' }}
                        >
                          <div style={{ fontWeight: 700, color: 'var(--text-white)', marginBottom: '0.25rem' }}>{priority.name}</div>
                          <div style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>{priority.reason}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div style={{ textAlign: 'center', padding: '3rem 0' }}>
                <p style={{ color: 'var(--text-muted)' }}>No analysis available. Run a discovery or audit to generate insights.</p>
              </div>
            )}
          </section>
        </div>
      </main>

      <AIChat sidebarCollapsed={isSidebarCollapsed} />
    </div>
  );
}
