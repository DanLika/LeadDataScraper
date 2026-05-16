'use client';

import { useCallback, useState, useEffect } from 'react';
import {
  TrendingUp, Shield, Users, Target, Zap, ArrowLeft, Loader2, Menu
} from 'lucide-react';
import {
  Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip,
  ResponsiveContainer, PieChart, Pie, Legend, BarChart
} from 'recharts';
import Link from 'next/link';
import Sidebar from '../components/Sidebar';
import AIChat from '../components/AIChat';
import { API_BASE_URL, apiFetch } from '@/utils/apiConfig';
import { useEscape, restoreFocus, BURGER_SELECTOR } from '@/utils/useEscape';

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
  const [stats, setStats] = useState<Stats | null>(null);
  const [insights, setInsights] = useState<Insights | null>(null);
  const [leads, setLeads] = useState<Array<{ outreach_score?: number; high_risk_flag?: boolean }>>([]);
  const [loading, setLoading] = useState(true);
  const [fetchingInsights, setFetchingInsights] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  useEscape(() => {
    setIsSidebarOpen(false);
    restoreFocus(BURGER_SELECTOR);
  }, isSidebarOpen);
  const COLORS = ['var(--primary)', 'var(--success)', 'var(--warning)', 'var(--error)', 'var(--secondary)'];

  const fetchLeads = useCallback(async () => {
    try {
      const response = await apiFetch(`${API_BASE_URL}/leads`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setLeads(data.leads || []);
    } catch (err) {
      console.error('Error fetching leads:', err);
    }
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const response = await apiFetch(`${API_BASE_URL}/stats`);
      const data = await response.json();
      setStats(data);
    } catch (err) {
      console.error('Stats fetch failed:', err);
    }
  }, []);

  const fetchInsightsData = useCallback(async () => {
    setFetchingInsights(true);
    try {
      const response = await apiFetch(`${API_BASE_URL}/insights`);
      const data = await response.json();
      setInsights(data);
    } catch (err) {
      console.error('Insights fetch failed:', err);
    } finally {
      setFetchingInsights(false);
    }
  }, []);

  useEffect(() => {
    const init = async () => {
      await Promise.all([fetchLeads(), fetchStats(), fetchInsightsData()]);
      setLoading(false);
    }
    init();
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
        setView={() => {}}
        showDiscoveryModal={false}
        setShowDiscoveryModal={() => {}}
        showSettings={false}
        setShowSettings={() => {}}
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
            <div className="card stat-card">
              <div className="stat-icon" style={{ background: 'var(--primary-tint-10)', color: 'var(--primary)' }}>
                <Users size={20} />
              </div>
              <div>
                <p className="stat-label">Total Leads</p>
                <div className="stat-value">{stats?.total_leads || 0}</div>
              </div>
            </div>
            <div className="card stat-card">
              <div className="stat-icon" style={{ background: 'var(--success-tint)', color: 'var(--success)' }}>
                <Shield size={20} />
              </div>
              <div>
                <p className="stat-label">Audited Leads</p>
                <div className="stat-value">
                  {stats?.audit_status_distribution?.find(s => s.name === 'completed')?.value || 0}
                </div>
              </div>
            </div>
            <div className="card stat-card">
              <div className="stat-icon" style={{ background: 'var(--warning-tint)', color: 'var(--warning)' }}>
                <Target size={20} />
              </div>
              <div>
                <p className="stat-label">Top Prospects</p>
                <div className="stat-value">
                  {leads.filter(l => (l.outreach_score || 0) > 70).length}
                </div>
              </div>
            </div>
            <div className="card stat-card">
              <div className="stat-icon" style={{ background: 'var(--error-tint)', color: 'var(--error)' }}>
                <Zap size={20} />
              </div>
              <div>
                <p className="stat-label">High Risk</p>
                <div className="stat-value">
                  {leads.filter(l => l.high_risk_flag).length}
                </div>
              </div>
            </div>
          </section>

          {/* Charts Row 1 */}
          <div className="grid-responsive-2" style={{ marginBottom: '2rem' }}>
            <div className="card">
              <h2 className="card-title" style={{ marginBottom: '1.5rem' }}>Audit Status Breakdown</h2>
              <div style={{ width: '100%' }} role="img" aria-label="Audit status distribution chart">
                <ResponsiveContainer width="100%" height={300}>
                  <PieChart>
                    <Pie
                      data={(stats?.audit_status_distribution || []).map((d, i) => ({ ...d, fill: COLORS[i % COLORS.length] }))}
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
                    <Legend verticalAlign="bottom" height={36}/>
                  </PieChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="card">
              <h2 className="card-title" style={{ marginBottom: '1.5rem' }}>SEO Score Distribution</h2>
              <div style={{ width: '100%' }} role="img" aria-label="SEO score distribution chart">
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
                        <div key={idx} style={{ padding: '1rem', background: 'var(--surface-muted)', borderRadius: '12px', border: '1px solid var(--border-subtle)', transition: 'border-color 0.2s' }}>
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
