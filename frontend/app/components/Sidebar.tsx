'use client';

import { useState, useEffect } from 'react';
import {
  BarChart3, Search, CheckCircle, AlertTriangle, Settings,
  Zap, RefreshCw, Loader2, Shield, ChevronLeft, ChevronRight,
  TrendingUp, X
} from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

interface SidebarLead {
  company_name?: string;
  name?: string;
  outreach_score?: number;
}

interface SidebarInsights {
  summary: string;
  insights: string[];
  top_priorities: Array<{ name: string; reason: string }>;
}

interface SidebarProps {
  view: 'all' | 'audited' | 'high-risk';
  setView: (view: 'all' | 'audited' | 'high-risk') => void;
  showDiscoveryModal: boolean;
  setShowDiscoveryModal: (show: boolean) => void;
  showSettings: boolean;
  setShowSettings: (show: boolean) => void;
  leads: SidebarLead[];
  fetchingInsights: boolean;
  insights: SidebarInsights | null;
  fetchInsights: () => void;
  setSearchTerm: (term: string) => void;
  isOpenMobile?: boolean;
  setIsOpenMobile?: (open: boolean) => void;
  onCollapsedChange?: (collapsed: boolean) => void;
}

export default function Sidebar({
  view,
  setView,
  showDiscoveryModal,
  setShowDiscoveryModal,
  showSettings,
  setShowSettings,
  leads,
  fetchingInsights,
  insights,
  fetchInsights,
  setSearchTerm,
  isOpenMobile,
  setIsOpenMobile,
  onCollapsedChange
}: SidebarProps) {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [isManuallyToggled, setIsManuallyToggled] = useState(false);
  const pathname = usePathname();

  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    let timeout: NodeJS.Timeout;
    const check = () => {
      clearTimeout(timeout);
      timeout = setTimeout(() => setIsMobile(window.innerWidth <= 1024), 150);
    };
    check();
    window.addEventListener('resize', check);
    return () => { window.removeEventListener('resize', check); clearTimeout(timeout); };
  }, []);

  const toggleSidebar = () => {
    const next = !isCollapsed;
    setIsCollapsed(next);
    setIsManuallyToggled(true);
    onCollapsedChange?.(next);
  };

  const isInsightsPage = pathname === '/insights';

  // On mobile, always show labels (never collapsed)
  // We use both JS and CSS approach for robustness
  const showLabels = isOpenMobile ? true : !isCollapsed;
  const showExtra = isOpenMobile ? true : !isCollapsed;

  return (
    <>
      {/* Mobile Overlay Backdrop */}
      {isOpenMobile && (
        <div
          className="sidebar-mobile-backdrop"
          onClick={() => setIsOpenMobile?.(false)}
        />
      )}

      <aside aria-label="Main navigation" className={`sidebar ${isCollapsed && !isMobile ? 'collapsed' : 'expanded'} ${isManuallyToggled && !isCollapsed ? 'user-expanded' : ''} ${isOpenMobile ? 'mobile-open' : ''}`}>
        {!isMobile && (
          <button
            className="sidebar-toggle"
            onClick={toggleSidebar}
            aria-label={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {isCollapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          </button>
        )}

        <div className="sidebar-scroll-content">
          <div className="sidebar-logo">
            <Link href="/" className="logo-link" style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', textDecoration: 'none', color: 'inherit' }}>
              <div className="logo-icon">
                <Shield size={24} color="white" />
              </div>
              {showLabels && <strong className="logo-text">LeadScout</strong>}
            </Link>
            {isOpenMobile && (
              <button
                className="mobile-close-btn"
                onClick={() => setIsOpenMobile?.(false)}
                aria-label="Close menu"
                style={{ background: 'var(--surface-muted)', border: '1px solid var(--border-subtle)', borderRadius: '8px', padding: '0.5rem', color: 'var(--text-muted)', cursor: 'pointer', marginLeft: 'auto', minWidth: '44px', minHeight: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
              >
                <X size={20} />
              </button>
            )}
          </div>

          <nav className="sidebar-nav" aria-label="Primary navigation">
            <Link
              href="/"
              className={`nav-item ${!isInsightsPage && view === 'all' && !showDiscoveryModal && !showSettings ? 'active' : ''}`}
              onClick={() => { setView('all'); setShowDiscoveryModal(false); setShowSettings(false); setIsOpenMobile?.(false); }}
              title="Dashboard"
              aria-current={!isInsightsPage && view === 'all' && !showDiscoveryModal && !showSettings ? 'page' : undefined}
            >
              <BarChart3 size={18} />
              {showLabels && <span>Dashboard</span>}
            </Link>
            <Link
              href="/insights"
              className={`nav-item ${isInsightsPage ? 'active' : ''}`}
              onClick={() => { setIsOpenMobile?.(false); }}
              title="Strategic Insights"
              aria-current={isInsightsPage ? 'page' : undefined}
            >
              <TrendingUp size={18} />
              {showLabels && <span>Insights</span>}
            </Link>
            <button
              className={`nav-item ${showDiscoveryModal ? 'active' : ''}`}
              onClick={() => { setShowDiscoveryModal(true); setShowSettings(false); setIsOpenMobile?.(false); }}
              title="Deep Discovery"
            >
              <Search size={18} />
              {showLabels && <span>Deep Discovery</span>}
            </button>
            <button
              className={`nav-item ${!isInsightsPage && view === 'audited' && !showDiscoveryModal && !showSettings ? 'active' : ''}`}
              onClick={() => { setView('audited'); setShowDiscoveryModal(false); setShowSettings(false); setIsOpenMobile?.(false); }}
              title="Audited"
            >
              <CheckCircle size={18} />
              {showLabels && <span>Audited</span>}
            </button>
            <button
              className={`nav-item ${!isInsightsPage && view === 'high-risk' && !showDiscoveryModal && !showSettings ? 'active' : ''}`}
              style={{ color: view === 'high-risk' ? 'var(--error)' : 'inherit' }}
              onClick={() => { setView('high-risk'); setShowDiscoveryModal(false); setShowSettings(false); setIsOpenMobile?.(false); }}
              title="High Risk"
            >
              <AlertTriangle size={18} />
              {showLabels && <span>High Risk</span>}
            </button>
            <button
              className={`nav-item ${showSettings ? 'active' : ''}`}
              onClick={() => { setShowSettings(true); setShowDiscoveryModal(false); setIsOpenMobile?.(false); }}
              title="Settings"
            >
              <Settings size={18} />
              {showLabels && <span>Settings</span>}
            </button>
          </nav>

          {showExtra && (
            <div className="sidebar-extra-content">
              <div className="sidebar-section">
                <div className="prospects-widget">
                  <div className="widget-header">
                      <Zap size={14} color="var(--primary)" />
                      <span className="widget-title">TOP PROSPECTS</span>
                  </div>
                  <div className="widget-content">
                    {leads
                      .filter(l => (l.outreach_score || 0) > 0)
                      .sort((a, b) => (b.outreach_score || 0) - (a.outreach_score || 0))
                      .slice(0, 3)
                      .map((lead, idx) => (
                        <div key={idx} className="prospect-item" role="button" tabIndex={0} onClick={() => { setSearchTerm(lead.company_name || lead.name || ''); setIsOpenMobile?.(false); }} onKeyDown={(e: React.KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSearchTerm(lead.company_name || lead.name || ''); setIsOpenMobile?.(false); } }}>
                            <span className="prospect-name truncate">{lead.company_name || lead.name}</span>
                            <span className="prospect-score">{lead.outreach_score}</span>
                        </div>
                      ))
                    }
                    {leads.filter(l => (l.outreach_score || 0) > 0).length === 0 && (
                      <p className="empty-widget">Enrich leads to see rankings.</p>
                    )}
                  </div>
                </div>
              </div>

              <div className="sidebar-section">
                <div className="insights-header">
                  <h4 className="section-title">AI Insights</h4>
                  <button 
                    onClick={fetchInsights} 
                    className={`refresh-btn ${fetchingInsights ? 'animate-spin' : ''}`}
                    disabled={fetchingInsights}
                  >
                    <RefreshCw size={14} />
                  </button>
                </div>
                
                {fetchingInsights && !insights ? (
                  <div className="insights-loading">
                    <Loader2 className="animate-spin" size={20} color="var(--primary)" />
                  </div>
                ) : insights ? (
                  <div className="insights-content">
                    <div className="insights-summary">
                      <p>{insights.summary}</p>
                    </div>
                    
                    <ul className="insights-list">
                      {Array.isArray(insights.insights) && insights.insights.map((insight: string, idx: number) => (
                        <li key={idx} className="insight-item">
                          <span className="bullet">•</span> {insight}
                        </li>
                      ))}
                    </ul>

                    {Array.isArray(insights.top_priorities) && insights.top_priorities.length > 0 && (
                      <div className="priorities-section">
                        <p className="priorities-title">Priority Outreach</p>
                        {insights.top_priorities.map((p: { name: string; reason: string }, idx: number) => (
                          <div key={idx} className="priority-card">
                            <div className="priority-name">{p.name}</div>
                            <div className="priority-reason">{p.reason}</div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="empty-insights">
                    No insights generated. Refresh to analyze leads.
                  </div>
                )}
              </div>

              <div className="sidebar-footer">
                <div className="guide-card">
                  <h4 className="guide-title">Quick Guide</h4>
                  <ul className="guide-list">
                    <li><strong>1. Import:</strong> Load a CSV of raw leads.</li>
                    <li><strong>2. Discovery:</strong> Use &quot;Deep Discovery&quot; to find more.</li>
                    <li><strong>3. Orchestrate:</strong> Hit &quot;AI Orchestrate&quot; for full audit.</li>
                    <li><strong>4. Outreach:</strong> AI generates personalized hooks.</li>
                  </ul>
                </div>
              </div>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
