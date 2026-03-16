'use client';

import { useCallback, useState, useEffect, useMemo } from 'react';
import { 
  BarChart3, TrendingUp, Shield, Users, Target, Zap, ArrowLeft, Loader2, Menu
} from 'lucide-react';
import { 
  Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, 
  ResponsiveContainer, PieChart, Pie, Cell, Legend, BarChart
} from 'recharts';
import Link from 'next/link';
import Sidebar from '../components/Sidebar';
import AIChat from '../components/AIChat';
import { API_BASE_URL } from '@/utils/apiConfig';
import { createClient } from '@/utils/supabase/client';

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
  const [leads, setLeads] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchingInsights, setFetchingInsights] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const supabase = useMemo(() => createClient(), []);

  const COLORS = ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];

  const fetchLeads = useCallback(async () => {
    const { data } = await supabase
      .from('leads')
      .select('*')
      .order('created_at', { ascending: false });
    setLeads(data || []);
  }, [supabase]);

  const fetchStats = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/stats`);
      const data = await response.json();
      setStats(data);
    } catch (err) {
      console.error('Stats fetch failed:', err);
    }
  }, []);

  const fetchInsightsData = useCallback(async () => {
    setFetchingInsights(true);
    try {
      const response = await fetch(`${API_BASE_URL}/insights`);
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
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', background: '#0a0a0c' }}>
        <Loader2 className="animate-spin" size={48} style={{ color: '#6366f1' }} />
      </div>
    );
  }

  return (
    <div className="dashboard-container">
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

      <main className="main-content" style={{ padding: 0, display: 'flex', flexDirection: 'column' }}>
        <div className="mobile-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <div className="logo-icon" style={{ width: '32px', height: '32px', borderRadius: '8px' }}>
              <Shield size={18} color="white" />
            </div>
            <strong style={{ fontSize: '1rem', letterSpacing: '-0.02em' }}>LeadScout</strong>
          </div>
          <button
            onClick={() => setIsSidebarOpen(true)}
            style={{ background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '10px', padding: '0.5rem', cursor: 'pointer', color: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            aria-label="Open menu"
          >
            <Menu size={22} />
          </button>
        </div>

        <header className="main-header" style={{ padding: '2rem' }}>
          <div className="header-left">
            <Link href="/" className="back-link" style={{ marginRight: '1rem', color: '#94a3b8' }}>
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
              <div className="stat-icon" style={{ background: 'rgba(99, 102, 241, 0.1)', color: '#6366f1' }}>
                <Users size={20} />
              </div>
              <div>
                <p className="stat-label">Total Leads</p>
                <h3 className="stat-value">{stats?.total_leads || 0}</h3>
              </div>
            </div>
            <div className="card stat-card">
              <div className="stat-icon" style={{ background: 'rgba(16, 185, 129, 0.1)', color: '#10b981' }}>
                <Shield size={20} />
              </div>
              <div>
                <p className="stat-label">Audited Leads</p>
                <h3 className="stat-value">
                  {stats?.audit_status_distribution?.find(s => s.name === 'completed')?.value || 0}
                </h3>
              </div>
            </div>
            <div className="card stat-card">
              <div className="stat-icon" style={{ background: 'rgba(245, 158, 11, 0.1)', color: '#f59e0b' }}>
                <Target size={20} />
              </div>
              <div>
                <p className="stat-label">Top Prospects</p>
                <h3 className="stat-value">
                  {leads.filter(l => (l.outreach_score || 0) > 70).length}
                </h3>
              </div>
            </div>
            <div className="card stat-card">
              <div className="stat-icon" style={{ background: 'rgba(239, 68, 68, 0.1)', color: '#ef4444' }}>
                <Zap size={20} />
              </div>
              <div>
                <p className="stat-label">High Risk</p>
                <h3 className="stat-value">
                  {leads.filter(l => l.high_risk_flag).length}
                </h3>
              </div>
            </div>
          </section>

          {/* Charts Row 1 */}
          <div className="grid-responsive-2" style={{ marginBottom: '2rem' }}>
            <div className="card">
              <h3 className="card-title" style={{ marginBottom: '1.5rem' }}>Audit Status Breakdown</h3>
              <div style={{ height: '300px', width: '100%' }}>
                <ResponsiveContainer width="100%" height="100%" minHeight={200}>
                  <PieChart>
                    <Pie
                      data={stats?.audit_status_distribution || []}
                      cx="50%"
                      cy="50%"
                      innerRadius={60}
                      outerRadius={100}
                      paddingAngle={5}
                      dataKey="value"
                    >
                      {(stats?.audit_status_distribution || []).map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                      ))}
                    </Pie>
                    <RechartsTooltip 
                      contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: '8px' }}
                    />
                    <Legend verticalAlign="bottom" height={36}/>
                  </PieChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="card">
              <h3 className="card-title" style={{ marginBottom: '1.5rem' }}>SEO Score Distribution</h3>
              <div style={{ height: '300px', width: '100%' }}>
                <ResponsiveContainer width="100%" height="100%" minHeight={200}>
                  <BarChart data={stats?.seo_score_ranges || []}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
                    <XAxis 
                      dataKey="range" 
                      axisLine={false} 
                      tickLine={false} 
                      tick={{ fill: '#94a3b8', fontSize: 12 }} 
                    />
                    <YAxis 
                      axisLine={false} 
                      tickLine={false} 
                      tick={{ fill: '#94a3b8', fontSize: 12 }} 
                    />
                    <RechartsTooltip 
                      cursor={{ fill: 'rgba(255,255,255,0.05)' }}
                      contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: '8px' }}
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
              <div className="stat-icon" style={{ background: 'rgba(99, 102, 241, 0.1)', color: '#6366f1', width: '32px', height: '32px' }}>
                <TrendingUp size={18} />
              </div>
              <h3 className="card-title" style={{ marginBottom: 0 }}>AI Strategic Analysis</h3>
            </div>
            
            {fetchingInsights ? (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '3rem 0', gap: '1rem' }}>
                <Loader2 className="animate-spin" size={32} style={{ color: '#6366f1' }} />
                <p style={{ color: '#94a3b8' }}>Analyzing market patterns...</p>
              </div>
            ) : insights ? (
              <div className="insights-full-view">
                <div className="summary-banner" style={{ padding: '1.5rem', background: 'rgba(99, 102, 241, 0.05)', borderRadius: '12px', border: '1px solid rgba(99, 102, 241, 0.1)', marginBottom: '2rem' }}>
                  <p style={{ fontSize: '1.125rem', color: '#a5b4fc', lineHeight: 1.6, margin: 0 }}>
                    {insights.summary}
                  </p>
                </div>

                <div className="grid-responsive-2">
                  <div>
                    <h4 style={{ fontSize: '1rem', fontWeight: 700, marginBottom: '1rem', color: '#f8fafc' }}>Key Market Patterns</h4>
                    <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                      {insights.insights.map((insight, idx) => (
                        <li key={idx} style={{ display: 'flex', gap: '0.75rem', padding: '1rem', background: 'rgba(255,255,255,0.05)', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.05)' }}>
                          <span style={{ flexShrink: 0, width: '1.5rem', height: '1.5rem', borderRadius: '50%', background: 'rgba(99,102,241,0.2)', color: '#818cf8', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.75rem', fontWeight: 700 }}>
                            {idx + 1}
                          </span>
                          <span style={{ color: '#cbd5e1' }}>{insight}</span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div>
                    <h4 style={{ fontSize: '1rem', fontWeight: 700, marginBottom: '1rem', color: '#f8fafc' }}>High-Impact Priorities</h4>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                      {insights.top_priorities.map((priority, idx) => (
                        <div key={idx} style={{ padding: '1rem', background: 'rgba(255,255,255,0.05)', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.05)', transition: 'border-color 0.2s' }}>
                          <div style={{ fontWeight: 700, color: 'white', marginBottom: '0.25rem' }}>{priority.name}</div>
                          <div style={{ fontSize: '0.875rem', color: '#94a3b8' }}>{priority.reason}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div style={{ textAlign: 'center', padding: '3rem 0' }}>
                <p style={{ color: '#94a3b8' }}>No analysis available. Run a discovery or audit to generate insights.</p>
              </div>
            )}
          </section>
        </div>
      </main>

      <AIChat sidebarCollapsed={isSidebarCollapsed} />
    </div>
  );
}
