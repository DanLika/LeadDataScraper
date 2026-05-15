'use client';

import { useCallback, useState, useEffect, Fragment, useMemo, useRef } from 'react';
import { useFocusTrap } from '@/utils/useFocusTrap';
import {
  Upload, Globe, Mail, Phone, Shield,
  Settings, AlertCircle, AlertTriangle,
  Download, FileDown, Crosshair,
  Users, Loader2, Play, RefreshCw, X, Zap,
  Copy, Check, Menu,
  Facebook, Instagram, Linkedin, Music, Pin
} from 'lucide-react';
import AIChat from './components/AIChat';
import Sidebar from './components/Sidebar';
import HealthChart from './components/HealthChart';
import StatsCards from './components/StatsCards';
import FilterBar from './components/FilterBar';
import { API_BASE_URL, apiFetch } from '@/utils/apiConfig';

interface Lead {
  id?: string;
  unique_key: string;
  company_name?: string;
  name?: string;
  website?: string;
  audit_status?: string;
  retry_count: number;
  audit_results?: {
    score: number;
    high_risk_flag?: boolean;
  };
  high_risk_flag?: boolean;
  facebook?: string;
  instagram?: string;
  linkedin?: string;
  tiktok?: string;
  pinterest?: string;
  company_size?: string;
  target_clients?: string;
  business_details?: string;
  leadership_team?: string;
  key_offerings?: string;
  last_error?: string;
  outreach_score?: number;
  phone?: string;
  segment?: string;
  linkedin_hook?: string;
  email_hook?: string;
  email?: string;
  pain_points?: string;
}

interface OrchestratorJob {
  id: string;
  status: string;
  processed_count: number;
  total_count: number;
  type: string;
  current_phase?: string;
}

interface Insights {
  summary: string;
  insights: string[];
  top_priorities: Array<{ name: string; reason: string }>;
}

interface AuditStatusInfo {
  active: boolean;
  hunting?: boolean;
  current_chunk?: number;
  processed?: number;
  total?: number;
}

interface ExecutePlan {
  task: string;
  params?: Record<string, string | number | boolean>;
}

interface CampaignItem {
  company: string;
  first_name?: string;
  draft: string;
}

const ALLOWED_UPLOAD_TYPES = ['text/csv', 'application/vnd.ms-excel'];
const MAX_UPLOAD_SIZE = 10 * 1024 * 1024; // 10MB
const DISCOVERY_STEPS = [
  "Initializing Google Maps crawler...",
  "Navigating to search results...",
  "Scanning business cards...",
  "Extracting websites and phone numbers...",
  "Syncing new leads to inventory..."
];

// Strip markdown markers for clean display
function cleanMarkdown(text: string): string {
  return text
    .replace(/^###?\s*/gm, '')       // Remove ### headers
    .replace(/\*\*([^*]+)\*\*/g, '$1') // Remove **bold** markers
    .replace(/^\*\s+/gm, '• ')       // Convert * list items to bullets
    .replace(/\n{3,}/g, '\n\n')      // Collapse excess newlines
    .trim();
}

// Collapsible text component for long content
function CollapsibleText({ text, maxLength = 250, style }: { text: string; maxLength?: number; style?: React.CSSProperties }) {
  const [expanded, setExpanded] = useState(false);
  const cleaned = useMemo(() => cleanMarkdown(text), [text]);
  const isLong = cleaned.length > maxLength;
  const display = isLong && !expanded ? cleaned.slice(0, maxLength) + '...' : cleaned;

  return (
    <div>
      <p className="text-wrap" style={{ ...style, margin: 0, whiteSpace: 'pre-line' }}>{display}</p>
      {isLong && (
        <button
          onClick={() => setExpanded(!expanded)}
          style={{ background: 'none', border: 'none', color: 'var(--primary-strong)', fontSize: '0.7rem', cursor: 'pointer', padding: '0.25rem 0', fontWeight: 600 }}
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  );
}

// No longer need AuditStatus interface separately as it's merged into backend fallback
export default function Dashboard() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(true);
  const [outreachDraft, setOutreachDraft] = useState<{ text: string, leadName: string } | null>(null);
  const [linkedinDraft, setLinkedinDraft] = useState<string>('');
  const [isDrafting, setIsDrafting] = useState(false);
  const [insights, setInsights] = useState<Insights | null>(null);
  const [fetchingInsights, setFetchingInsights] = useState(false);
  const [auditStatus, setAuditStatus] = useState<AuditStatusInfo | null>(null);
  const [showDiscoveryModal, setShowDiscoveryModal] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const settingsModalRef = useRef<HTMLDivElement>(null);
  const discoveryModalRef = useRef<HTMLDivElement>(null);
  const outreachModalRef = useRef<HTMLDivElement>(null);
  const campaignModalRef = useRef<HTMLDivElement>(null);
  const [discoveryQuery, setDiscoveryQuery] = useState('');
  const [discoveryLocation, setDiscoveryLocation] = useState('');
  const [isDiscovering, setIsDiscovering] = useState(false);
  const [orchestratorJob, setOrchestratorJob] = useState<OrchestratorJob | null>(null);
  const [, setProcessingAi] = useState(false);
  const [processingLeads, setProcessingLeads] = useState<Record<string, boolean>>({});
  const [activeLead, setActiveLead] = useState<Lead | null>(null);
  const [browserPersistence, setBrowserPersistence] = useState(true);
  const [view, setView] = useState<'all' | 'audited' | 'high-risk'>('all');
  const [searchTerm, setSearchTerm] = useState('');
  const [campaign, setCampaign] = useState<CampaignItem[] | null>(null);
  useFocusTrap(settingsModalRef, showSettings);
  useFocusTrap(discoveryModalRef, showDiscoveryModal);
  useFocusTrap(outreachModalRef, !!outreachDraft);
  useFocusTrap(campaignModalRef, !!campaign);
  const [filterSegment, setFilterSegment] = useState<string>('all');
  const [filterMinScore, setFilterMinScore] = useState<number>(0);
  const [filterAuditStatus, setFilterAuditStatus] = useState<string>('all');
  const [copiedHookType, setCopiedHookType] = useState<'email' | 'linkedin' | null>(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [discoveryStep, setDiscoveryStep] = useState(0);
  const [toasts, setToasts] = useState<Array<{ id: number; message: string; type: 'success' | 'error' | 'info' }>>([]);

  const showToast = useCallback((message: string, type: 'success' | 'error' | 'info' = 'info') => {
    const id = Date.now();
    setToasts(prev => [...prev, { id, message, type }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 3500);
  }, []);

  // ESC key handler for all modals
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (campaign) setCampaign(null);
        else if (outreachDraft) setOutreachDraft(null);
        else if (showSettings) setShowSettings(false);
        else if (showDiscoveryModal && !isDiscovering) setShowDiscoveryModal(false);
      }
    };
    document.addEventListener('keydown', handleEsc);
    return () => document.removeEventListener('keydown', handleEsc);
  }, [campaign, outreachDraft, showSettings, showDiscoveryModal, isDiscovering]);

  const fetchLeads = useCallback(async () => {
    try {
      const response = await apiFetch(`${API_BASE_URL}/leads`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setLeads(data.leads || []);
    } catch (err) {
      console.error('Error fetching leads:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchInsights = useCallback(async () => {
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
    fetchLeads();
    fetchInsights();

    // Poll the backend for fresh leads instead of subscribing via the browser
    // Supabase client. Supabase Realtime requires anon access to the table,
    // which is intentionally disabled by RLS — backend is now the only reader.
    const interval = setInterval(() => {
      fetchLeads();
    }, 15000);

    return () => clearInterval(interval);
  }, [fetchLeads, fetchInsights]);

  // Combined status monitoring for legacy endpoints
  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (auditStatus?.active && !orchestratorJob) {
      interval = setInterval(async () => {
        try {
          const res = await apiFetch(`${API_BASE_URL}/audit-status`);
          const data = await res.json();
          setAuditStatus(data);
          if (!data.active) {
             fetchLeads();
             fetchInsights();
          }
        } catch (err) {
          console.error('Status fetch failed:', err);
        }
      }, 3000);
    }
    return () => clearInterval(interval!);
  }, [auditStatus?.active, orchestratorJob, fetchLeads, fetchInsights]);

  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (orchestratorJob && (orchestratorJob.status === 'running' || orchestratorJob.status === 'starting')) {
      interval = setInterval(async () => {
        try {
          const res = await apiFetch(`${API_BASE_URL}/orchestrator/status/${orchestratorJob.id}`);
          const data = await res.json();
          setOrchestratorJob(data);
          
          if (data.status === 'completed' || data.status === 'failed') {
            fetchLeads();
            fetchInsights();
            if (data.current_phase !== 'CAPTCHA Required') {
              // Only stop tracking if it's not waiting for CAPTCHA (or if we consider 'failed' as final)
              // For now, discovery marks as failed on CAPTCHA.
            }
          }
        } catch (err) {
          console.error('Orchestrator status fetch failed:', err);
        }
      }, 3000);
    }
    return () => clearInterval(interval!);
  }, [orchestratorJob, fetchLeads, fetchInsights]);

  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (isDiscovering) {
      setDiscoveryStep(0);
      interval = setInterval(() => {
        setDiscoveryStep(prev => (prev + 1) % DISCOVERY_STEPS.length);
      }, 3000);
    } else {
      setDiscoveryStep(0);
    }
    return () => clearInterval(interval!);
  }, [isDiscovering]);

  const handleExecutePlan = async (plan: ExecutePlan) => {
    if (!plan) return;
    setProcessingAi(true);
    try {
      const response = await apiFetch(`${API_BASE_URL}/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(plan),
      });
      const data = await response.json();
      
      // Update local state if needed based on the task
      if (data.result?.job_id) {
        setOrchestratorJob({ 
          id: data.result.job_id, 
          status: 'starting', 
          processed_count: 0, 
          total_count: 0, 
          type: plan.task === 'SEO_AUDIT' ? 'audit' : 
                plan.task === 'DISCOVERY_SEARCH' ? 'discovery' : 
                plan.task === 'DEEP_HUNT' ? 'hunt' : 'massive' 
        });
        
        if (plan.task === 'DISCOVERY_SEARCH') {
          setDiscoveryQuery(String(plan.params?.query ?? ''));
          setDiscoveryLocation(String(plan.params?.location ?? ''));
          setIsDiscovering(true);
          setShowDiscoveryModal(true);
        }
      }

      // Automatically trigger modals for drafts
      if (data.result?.draft) {
        if (plan.task === 'OUTREACH_DRAFT') {
          setOutreachDraft({ text: data.result.draft, leadName: data.result.lead_name || 'Prospect' });
        } else if (plan.task === 'LINKEDIN_DRAFT') {
          setLinkedinDraft(data.result.draft);
          setOutreachDraft({ text: data.result.draft, leadName: data.result.recipient || 'Prospect' });
        } else if (plan.task === 'GET_INSIGHTS') {
          setInsights(data.result);
        } else if (plan.task === 'CAMPAIGN_STRATEGY') {
          setCampaign(data.result.campaign);
        }
      }

      return data.result;
    } catch (err) {
      console.error('Execute plan failed:', err);
      throw err;
    } finally {
      setProcessingAi(false);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!ALLOWED_UPLOAD_TYPES.includes(file.type) && !file.name.endsWith('.csv')) {
      showToast('Please upload a CSV file.', 'error');
      e.target.value = '';
      return;
    }
    if (file.size > MAX_UPLOAD_SIZE) {
      showToast('File is too large. Maximum size is 10MB.', 'error');
      e.target.value = '';
      return;
    }

    setLoading(true);
    const formData = new FormData();
    formData.append('file', file);

    try {
      const response = await apiFetch(`${API_BASE_URL}/upload`, {
        method: 'POST',
        body: formData,
      });
      if (!response.ok) {
        console.error('Upload failed with status:', response.status);
      }
      await response.json();
    } catch (err) {
      console.error('Upload failed:', err);
    } finally {
      setLoading(false);
      e.target.value = '';
    }
  };

  const processLead = async (uniqueKey: string) => {
    setProcessingLeads(prev => ({ ...prev, [uniqueKey]: true }));
    try {
      const resp = await apiFetch(`${API_BASE_URL}/process-lead`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ unique_key: uniqueKey }),
      });
      const data = await resp.json();
      if (data.job_id) {
        setOrchestratorJob({ id: data.job_id, status: 'starting', processed_count: 0, total_count: 1, type: 'audit' });
      }
    } catch (err) {
      console.error('Process lead failed:', err);
    } finally {
      setProcessingLeads(prev => ({ ...prev, [uniqueKey]: false }));
    }
  };

  const processAll = async () => {
    setLoading(true);
    try {
      const resp = await apiFetch(`${API_BASE_URL}/orchestrator/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: {}, tasks: ['audit'] }),
      });
      const data = await resp.json();
      if (data.job_id) {
        setOrchestratorJob({ id: data.job_id, status: 'starting', processed_count: 0, total_count: 0, type: 'audit' });
      }
    } catch (err) {
      console.error('Process all failed:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleDraftOutreach = async (lead: Lead) => {
    setIsDrafting(true);
    setActiveLead(lead);
    setOutreachDraft(null);
    setLinkedinDraft('');
    try {
      const res = await apiFetch(`${API_BASE_URL}/draft-outreach`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ unique_key: lead.unique_key })
      });
      const data = await res.json();
      if (data.draft) setOutreachDraft({ text: data.draft, leadName: lead.company_name || lead.name || 'Prospect' });
      
      // Also generate LinkedIn draft
      const liRes = await apiFetch(`${API_BASE_URL}/draft-linkedin`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ unique_key: lead.unique_key })
      });
      const liData = await liRes.json();
      if (liData.draft) setLinkedinDraft(liData.draft);
      
    } catch (error) {
      console.error("Outreach error:", error);
    } finally {
      setIsDrafting(false);
    }
  };

  const handleDeepHunt = async (uniqueKey: string) => {
    setProcessingLeads(prev => ({ ...prev, [uniqueKey]: true }));
    try {
      const resp = await apiFetch(`${API_BASE_URL}/hunt-lead`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ unique_key: uniqueKey }),
      });
      const data = await resp.json();
      if (data.job_id) {
        setOrchestratorJob({ id: data.job_id, status: 'starting', processed_count: 0, total_count: 1, type: 'hunt' });
      }
    } catch (err) {
      console.error('Deep hunt failed:', err);
    } finally {
      setProcessingLeads(prev => ({ ...prev, [uniqueKey]: false }));
    }
  };

  const handleDeepHuntAll = async () => {
    setLoading(true);
    try {
      const resp = await apiFetch(`${API_BASE_URL}/orchestrator/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: {}, tasks: ['hunt'] }),
      });
      const data = await resp.json();
      if (data.job_id) {
        setOrchestratorJob({ id: data.job_id, status: 'starting', processed_count: 0, total_count: 0, type: 'hunt' });
      }
    } catch (err) {
      console.error('Deep hunt all failed:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleStartDiscovery = async () => {
    if (!discoveryQuery.trim()) return;
    setIsDiscovering(true);
    try {
      const response = await apiFetch(`${API_BASE_URL}/discovery/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: discoveryQuery, location: discoveryLocation }),
      });
      const data = await response.json();
      if (data.job_id) {
        setOrchestratorJob({
          id: data.job_id,
          status: 'starting',
          current_phase: 'Initializing...',
          type: 'discovery',
          processed_count: 0,
          total_count: 0
        });
      }
    } catch (err) {
      console.error('Discovery failed:', err);
      setIsDiscovering(false);
    }
  };
  const handleEnrichLead = async (uniqueKey: string) => {
    setProcessingLeads(prev => ({ ...prev, [uniqueKey]: true }));
    try {
      const resp = await apiFetch(`${API_BASE_URL}/enrich/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ unique_key: uniqueKey }),
      });
      const data = await resp.json();
      if (data.job_id) {
        setOrchestratorJob({ id: data.job_id, status: 'starting', processed_count: 0, total_count: 1, type: 'enrich' });
      }
    } catch (err) {
      console.error('Enrichment failed:', err);
    } finally {
      setProcessingLeads(prev => ({ ...prev, [uniqueKey]: false }));
    }
  };

  const handleClearLeads = async () => {
    if (!confirm("Are you SURE you want to clear all leads? This cannot be undone.")) return;
    setLoading(true);
    try {
      await apiFetch(`${API_BASE_URL}/leads/clear`, { method: 'DELETE' });
      setLeads([]);
      setInsights(null);
      showToast("All leads have been cleared.", 'success');
      setShowSettings(false);
    } catch (err) {
      console.error('Clear leads failed:', err);
    } finally {
      setLoading(false);
    }
  };

  const startMassivePipeline = async () => {
    setLoading(true);
    try {
      const resp = await apiFetch(`${API_BASE_URL}/orchestrator/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: {}, tasks: ['audit', 'enrich', 'hunt'] }),
      });
      const data = await resp.json();
      if (data.job_id) {
        setOrchestratorJob({ id: data.job_id, status: 'starting', processed_count: 0, total_count: 0, type: 'massive' });
      }
    } catch (err) {
      console.error('Failed to start massive pipeline:', err);
    } finally {
      setLoading(false);
    }
  };

  const stopOrchestratorJob = async () => {
    if (!orchestratorJob?.id) return;
    try {
      await apiFetch(`${API_BASE_URL}/orchestrator/stop/${orchestratorJob.id}`, { method: 'POST' });
      setOrchestratorJob({ ...orchestratorJob, status: 'stopped' });
    } catch (err) {
      console.error('Stop job failed:', err);
    }
  };

  const stopAuditProcess = async () => {
    try {
      await apiFetch(`${API_BASE_URL}/audit/stop`, { method: 'POST' });
      setAuditStatus({ ...auditStatus, active: false });
    } catch (err) {
      console.error('Stop audit failed:', err);
    }
  };
  const handleDownloadCsv = async () => {
    try {
      window.open(`${API_BASE_URL}/export/download`, '_blank');
    } catch (err) {
      console.error('Download failed:', err);
    }
  };
  const handleDownloadOutreachCsv = async () => {
    try {
      window.open(`${API_BASE_URL}/export/outreach`, '_blank');
    } catch (err) {
      console.error('CRM Download failed:', err);
    }
  };

  const ensureProtocol = (url: string) => {
    if (!url) return '';
    const trimmed = url.trim();
    if (!trimmed) return '';
    const candidate = /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
    try {
      const u = new URL(candidate);
      if (u.protocol !== 'http:' && u.protocol !== 'https:') return '';
      return u.toString();
    } catch {
      return '';
    }
  };

  const filteredLeads = useMemo(() => leads.filter((lead: Lead) => {
    const matchesSearch = (lead.company_name || lead.name || '').toLowerCase().includes(searchTerm.toLowerCase()) ||
                         (lead.website || '').toLowerCase().includes(searchTerm.toLowerCase());

    const matchesSegment = filterSegment === 'all' || lead.segment === filterSegment;
    const matchesScore = (lead.outreach_score || lead.audit_results?.score || 0) >= filterMinScore;
    const matchesAuditStatus = filterAuditStatus === 'all' || lead.audit_status === filterAuditStatus;

    const matchesAllFilters = matchesSegment && matchesScore && matchesAuditStatus;

    if (view === 'audited') return matchesSearch && lead.audit_status === 'Completed' && matchesAllFilters;
    if (view === 'high-risk') return matchesSearch && ((lead.audit_results?.score ?? 100) < 50 || lead.high_risk_flag || lead.audit_results?.high_risk_flag) && matchesAllFilters;
    return matchesSearch && matchesAllFilters;
  }), [leads, searchTerm, filterSegment, filterMinScore, filterAuditStatus, view]);

  const segmentOptions = useMemo(() =>
    Array.from(new Set(leads.map((l: Lead) => l.segment).filter(Boolean))),
  [leads]);

  return (
    <div className="dashboard-container">
      {/* Toast Notifications */}
      {toasts.length > 0 && (
        <div className="toast-container" role="status" aria-live="polite">
          {toasts.map(t => (
            <div key={t.id} className={`toast toast-${t.type}`}>{t.message}</div>
          ))}
        </div>
      )}
      <a href="#main-content" className="skip-link">Skip to main content</a>
      {/* Sidebar - Lead Insights */}
      <Sidebar
        view={view}
        setView={setView}
        showDiscoveryModal={showDiscoveryModal}
        setShowDiscoveryModal={setShowDiscoveryModal}
        showSettings={showSettings}
        setShowSettings={setShowSettings}
        leads={leads}
        fetchingInsights={fetchingInsights}
        insights={insights}
        fetchInsights={fetchInsights}
        setSearchTerm={setSearchTerm}
        isOpenMobile={isSidebarOpen}
        setIsOpenMobile={setIsSidebarOpen}
        onCollapsedChange={setIsSidebarCollapsed}
      />

      {/* Main Content */}
      <main id="main-content" tabIndex={-1} className="main-content" style={{ padding: 0, display: 'flex', flexDirection: 'column', outline: 'none' }}>
        {/* Mobile Header Toggle */}
        <div className="mobile-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <div className="logo-icon" style={{ width: '32px', height: '32px', borderRadius: '8px' }}>
              <Shield size={18} color="white" />
            </div>
            <strong style={{ fontSize: '1rem', letterSpacing: '-0.02em' }}>LeadScout</strong>
          </div>
          <button
            onClick={() => setIsSidebarOpen(true)}
            style={{ background: 'var(--surface-muted)', border: '1px solid var(--border-subtle)', borderRadius: '10px', padding: '0.5rem', cursor: 'pointer', color: 'var(--text-primary)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            aria-label="Open menu"
          >
            <Menu size={22} />
          </button>
        </div>
        {((orchestratorJob && (orchestratorJob.status === 'running' || orchestratorJob.status === 'starting')) || (auditStatus?.active && !orchestratorJob)) && (
          <div style={{ background: 'var(--primary-tint-10)', padding: '1rem 2.5rem', borderBottom: '1px solid var(--primary)', display: 'flex', alignItems: 'center', gap: '2rem', animation: 'fadeIn 0.3s ease' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', minWidth: '220px' }}>
              <RefreshCw size={18} className="animate-spin" color="var(--primary)" />
              <div style={{ display: 'flex', flexDirection: 'column' }}>
                <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>
                  {orchestratorJob ? (
                    orchestratorJob.type === 'discovery' ? 'Deep Discovery Active...' :
                    orchestratorJob.type === 'hunt' ? 'Deep Digital Hunting...' :
                    orchestratorJob.type === 'massive' ? 'Full Pipeline Orchestration...' :
                    orchestratorJob.type === 'enrich' ? 'Enriching Leads...' :
                    'Processing Intelligence...'
                  ) : (
                    auditStatus?.hunting ? 'Hunting Digital Footprints...' : 'Auditing Fleet...'
                  )}
                </span>
                <span style={{ fontSize: '0.7rem', color: 'var(--text-dim)' }}>
                  {orchestratorJob ? (orchestratorJob.current_phase || 'Initializing...') : `Chunk ${auditStatus?.current_chunk || 1} in progress`}
                </span>
              </div>
            </div>
            <div style={{ flex: 1, height: '8px', background: 'var(--surface-muted)', borderRadius: '4px', overflow: 'hidden', border: '1px solid var(--border-subtle)' }}>
              <div 
                style={{ 
                   height: '100%',
                   background: 'var(--primary)',
                   width: `${
                     orchestratorJob ? 
                     (orchestratorJob.total_count > 0 ? (orchestratorJob.processed_count / orchestratorJob.total_count) * 100 : 0) :
                     ((auditStatus?.total ?? 0) > 0 ? ((auditStatus?.processed ?? 0) / (auditStatus?.total ?? 1)) * 100 : 0)
                   }%`,
                   transition: 'width 0.8s cubic-bezier(0.4, 0, 0.2, 1)'
                }} 
              />
            </div>
            <div style={{ minWidth: '150px', fontSize: '0.85rem', color: 'var(--text-muted)', textAlign: 'right', fontFamily: 'monospace', display: 'flex', alignItems: 'center', gap: '1rem' }}>
               <span>
                 {orchestratorJob ? 
                   `${orchestratorJob.processed_count} / ${orchestratorJob.total_count} Leads` :
                   `${auditStatus?.processed || 0} / ${auditStatus?.total || 0} Leads`
                 }
               </span>
               <button 
                 onClick={orchestratorJob ? stopOrchestratorJob : stopAuditProcess}
                 aria-label="Stop processing"
                 style={{ background: 'var(--error-tint)', border: '1px solid var(--error)', color: 'var(--error)', borderRadius: '4px', padding: '0.35rem 0.75rem', minHeight: '44px', fontSize: '0.7rem', cursor: 'pointer' }}
               >
                 STOP
               </button>
            </div>
          </div>
        )}

        <div style={{ padding: '1rem 2rem 8rem 2rem' }} className="main-content-wrapper">
        <header className="page-header">
          <div style={{ minWidth: '300px' }}>
            <span style={{ fontSize: '0.8rem', fontWeight: 700, color: 'var(--primary-strong)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: '0.5rem', display: 'block' }}>Operational Overview</span>
            <h1 style={{ marginBottom: '0.5rem' }}>Pipeline Intelligence</h1>
            <p style={{ color: 'var(--text-muted)', fontSize: '1rem', fontWeight: 400 }}>Orchestrating AI-driven auditing for high-conversion prospecting.</p>
          </div>
          <div className="header-actions">
            <button 
              className="btn-secondary" 
              onClick={processAll}
              disabled={loading}
            >
              <Play size={18} /> Audit All
            </button>
            <button 
              className="btn-primary" 
              onClick={startMassivePipeline}
              disabled={loading || !!(orchestratorJob && (orchestratorJob.status === 'running' || orchestratorJob.status === 'starting'))}
              style={{ background: 'var(--primary)', border: 'none' }}
            >
              {orchestratorJob && (orchestratorJob.status === 'running' || orchestratorJob.status === 'starting') ? (
                <Loader2 size={18} className="animate-spin" />
              ) : (
                <Zap size={18} />
              )}
              AI Orchestrate
            </button>
            <button
              className="btn-secondary"
              onClick={handleDeepHuntAll}
              disabled={loading}
            >
              <Crosshair size={18} /> Hunt All
            </button>
            <input 
              type="file" 
              id="csv-upload" 
              accept=".csv" 
              style={{ display: 'none' }} 
              onChange={handleFileUpload}
            />
            <button 
              className="btn-secondary" 
              onClick={handleDownloadCsv}
              disabled={loading || leads.length === 0}
            >
              <Download size={18} /> Export Full
            </button>
            <button
              className="btn-secondary"
              onClick={handleDownloadOutreachCsv}
              disabled={loading || leads.length === 0}
            >
              <FileDown size={18} /> CRM Export
            </button>
            <button 
              className="btn-primary" 
              onClick={() => document.getElementById('csv-upload')?.click()}
              disabled={loading}
            >
              <Upload size={18} /> Import CSV
            </button>
          </div>
        </header>

        <HealthChart leads={leads} />

        <StatsCards leads={leads} />

        <div className="card card-no-hover" style={{ padding: '0', overflow: 'hidden' }}>
          <div className="table-container-wrapper" style={{ overflowX: 'auto', width: '100%' }}>
            <FilterBar
              searchTerm={searchTerm}
              setSearchTerm={setSearchTerm}
              filterSegment={filterSegment}
              setFilterSegment={setFilterSegment}
              filterAuditStatus={filterAuditStatus}
              setFilterAuditStatus={setFilterAuditStatus}
              filterMinScore={filterMinScore}
              setFilterMinScore={setFilterMinScore}
              segmentOptions={segmentOptions}
            />

            <div className="table-container">
              <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: '0', tableLayout: 'fixed' }}>
                <colgroup>
                  <col style={{ width: '25%' }} />
                  <col style={{ width: '14%' }} />
                  <col style={{ width: '14%' }} />
                  <col style={{ width: '20%' }} />
                  <col style={{ width: '27%' }} />
                </colgroup>
                <thead>
                  <tr style={{ background: 'var(--surface-subtle)' }}>
                    <th style={{ padding: '1rem 1.5rem', textAlign: 'left', fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)' }}>PROSPECT</th>
                    <th style={{ padding: '1rem 1rem', textAlign: 'center', fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>AUDIT STATUS</th>
                    <th style={{ padding: '1rem 1rem', textAlign: 'center', fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)' }}>INTELLIGENCE</th>
                    <th style={{ padding: '1rem 1rem', textAlign: 'center', fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)' }}>SOCIAL</th>
                    <th style={{ padding: '1rem 0.75rem', textAlign: 'right', fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-muted)' }}>ACTIONS</th>
                  </tr>
                </thead>
                <tbody>
                  {loading && leads.length === 0 ? (
                    <tr>
                      <td colSpan={5} style={{ padding: '4rem', textAlign: 'center' }}>
                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1rem' }}>
                          <Loader2 className="animate-spin" size={32} color="var(--primary)" />
                          <span style={{ color: 'var(--text-dim)' }}>Syncing with Supabase...</span>
                        </div>
                      </td>
                    </tr>
                  ) : filteredLeads.length === 0 ? (
                    <tr>
                      <td colSpan={5} style={{ padding: '4rem', textAlign: 'center', color: 'var(--text-dim)' }}>
                        <Users size={48} style={{ marginBottom: '1rem', opacity: 0.2 }} />
                        <p>{searchTerm ? `No leads matching "${searchTerm}" found.` : "No prospects discovered yet. Start by importing a CSV."}</p>
                      </td>
                    </tr>
                  ) : (
                    filteredLeads.map((lead: Lead) => (
                      <Fragment key={lead.unique_key}>
                        <tr className="table-row-hover" style={{ borderBottom: '1px solid var(--border)' }}>
                          <td style={{ padding: '1rem 1.5rem', verticalAlign: 'middle' }}>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                                <span style={{ fontWeight: 700, fontSize: '0.95rem', color: 'var(--text-white)' }}>{lead.company_name || lead.name || 'Unknown Entity'}</span>
                                {lead.high_risk_flag && (
                                  <span className="badge" style={{ background: 'var(--error-tint)', color: 'var(--error-strong)', border: '1px solid rgba(239, 68, 68, 0.25)' }}>
                                    <AlertCircle size={12} /> RISK
                                  </span>
                                )}
                              </div>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: 'var(--text-muted)', fontSize: '0.8rem' }}>
                                {lead.website && (
                                  <a href={ensureProtocol(lead.website)} target="_blank" rel="noopener noreferrer" style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', color: 'var(--primary-strong)', textDecoration: 'none', maxWidth: '250px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    <Globe size={14} style={{ flexShrink: 0 }} /> <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{lead.website.replace(/^https?:\/\//, '').replace(/\?.*$/, '')}</span>
                                  </a>
                                )}
                                {lead.phone && <span style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}><Phone size={14} /> {lead.phone}</span>}
                              </div>
                            </div>
                          </td>
                          <td style={{ padding: '1rem', textAlign: 'center', verticalAlign: 'middle' }}>
                            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.4rem' }}>
                              <span className={`badge ${lead.audit_status === 'Completed' ? 'badge-completed' : lead.audit_status?.includes('Failed') ? 'badge-error' : 'badge-pending'}`} style={{ whiteSpace: 'nowrap' }}>
                                {lead.audit_status || 'Unprocessed'}
                              </span>
                              {lead.audit_results?.score != null && (
                                <div style={{ fontSize: '0.7rem', fontWeight: 800, whiteSpace: 'nowrap', color: lead.audit_results.score < 50 ? 'var(--error-strong)' : 'var(--primary-strong)' }}>
                                  SEO: {lead.audit_results.score}/100
                                </div>
                              )}
                            </div>
                          </td>
                          <td style={{ padding: '1rem', textAlign: 'center', verticalAlign: 'middle' }}>
                             <div style={{ display: 'flex', justifyContent: 'center', gap: '0.5rem' }}>
                               {lead.linkedin_hook && <div title="LinkedIn Hook Ready" style={{ color: 'var(--primary)' }}><Linkedin size={16} /></div>}
                               {lead.email_hook && <div title="Email Hook Ready" style={{ color: 'var(--secondary)' }}><Mail size={16} /></div>}
                               {lead.audit_results?.high_risk_flag && <div title="Security Vulnerabilities" style={{ color: 'var(--error)' }}><Shield size={16} /></div>}
                             </div>
                          </td>
                          <td style={{ padding: '1rem', textAlign: 'center', verticalAlign: 'middle' }}>
                            <div style={{ display: 'flex', justifyContent: 'center', gap: '0.5rem', color: 'var(--text-dim)' }}>
                              {lead.facebook && <a href={ensureProtocol(lead.facebook)} target="_blank" rel="noopener noreferrer" aria-label={`${lead.company_name || lead.name || 'Lead'} Facebook page`} className="social-link"><Facebook size={16} /></a>}
                              {lead.instagram && <a href={ensureProtocol(lead.instagram)} target="_blank" rel="noopener noreferrer" aria-label={`${lead.company_name || lead.name || 'Lead'} Instagram page`} className="social-link"><Instagram size={16} /></a>}
                              {lead.linkedin && <a href={ensureProtocol(lead.linkedin)} target="_blank" rel="noopener noreferrer" aria-label={`${lead.company_name || lead.name || 'Lead'} LinkedIn page`} className="social-link"><Linkedin size={16} /></a>}
                              {lead.tiktok && <a href={ensureProtocol(lead.tiktok)} target="_blank" rel="noopener noreferrer" aria-label={`${lead.company_name || lead.name || 'Lead'} TikTok page`} className="social-link"><Music size={16} /></a>}
                              {lead.pinterest && <a href={ensureProtocol(lead.pinterest)} target="_blank" rel="noopener noreferrer" aria-label={`${lead.company_name || lead.name || 'Lead'} Pinterest page`} className="social-link"><Pin size={16} /></a>}
                              {!lead.facebook && !lead.instagram && !lead.linkedin && !lead.tiktok && !lead.pinterest && <span aria-label="No social links" style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>N/A</span>}
                            </div>
                          </td>
                          <td style={{ padding: '1rem 0.75rem', textAlign: 'right', verticalAlign: 'middle' }}>
                            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.4rem', flexWrap: 'wrap' }}>
                              <button 
                                className="btn-secondary" 
                                style={{ padding: '0.4rem', borderRadius: '8px', minWidth: '44px', minHeight: '44px' }}
                                onClick={() => handleEnrichLead(lead.unique_key)}
                                disabled={processingLeads[lead.unique_key]}
                                title="Harvest Contact Details"
                                aria-label="Harvest contact details"
                              >
                                {processingLeads[lead.unique_key] ? <Loader2 size={14} className="animate-spin" /> : <Users size={14} />}
                              </button>
                              <button 
                                className="btn-secondary" 
                                style={{ padding: '0.4rem', borderRadius: '8px', minWidth: '44px', minHeight: '44px', color: 'var(--accent)', borderColor: 'rgba(245, 158, 11, 0.2)' }}
                                onClick={() => handleDeepHunt(lead.unique_key)}
                                disabled={processingLeads[lead.unique_key]}
                                title="Deep Digital Hunt"
                                aria-label="Deep digital hunt"
                              >
                                {processingLeads[lead.unique_key] ? <Loader2 size={14} className="animate-spin" /> : <Crosshair size={14} />}
                              </button>
                              <button 
                                className="btn-primary" 
                                style={{ padding: '0.4rem 0.75rem', borderRadius: '8px', fontSize: '0.75rem' }}
                                onClick={() => handleDraftOutreach(lead)}
                                disabled={isDrafting || lead.audit_status !== 'Completed'}
                                title="Draft Personalised Outreach"
                              >
                                {isDrafting && activeLead?.unique_key === lead.unique_key ? <Loader2 size={14} className="animate-spin" /> : 'Draft'}
                              </button>
                              <button 
                                className="btn-primary" 
                                style={{ padding: '0.4rem 0.75rem', borderRadius: '8px', fontSize: '0.75rem', background: 'var(--secondary)' }}
                                onClick={() => processLead(lead.unique_key)}
                                disabled={processingLeads[lead.unique_key]}
                              >
                                {processingLeads[lead.unique_key] ? <Loader2 size={14} className="animate-spin" /> : lead.audit_status === 'Completed' ? 'Re-Audit' : 'Audit'}
                              </button>
                            </div>
                          </td>
                        </tr>
                        {(lead.last_error || (lead.key_offerings && lead.key_offerings !== 'Unknown') || (lead.pain_points && lead.pain_points !== 'Unknown')) && (
                          <tr style={{ background: 'var(--surface-subtle)' }}>
                            <td colSpan={5} style={{ padding: '1rem 2rem', borderBottom: '1px solid var(--border)' }}>
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
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </main>

      <AIChat onExecute={handleExecutePlan} sidebarCollapsed={isSidebarCollapsed} hidden={showSettings || !!outreachDraft || showDiscoveryModal || !!campaign} />

      {/* Outreach Draft Modal */}
      {outreachDraft && (
        <div ref={outreachModalRef} role="dialog" aria-modal="true" aria-labelledby="outreach-modal-title" className="modal-backdrop">
          <div className="card" style={{ width: '100%', maxWidth: 'min(600px, 95vw)', padding: 'clamp(1rem, 5vw, 2.5rem)', position: 'relative', border: '1px solid var(--primary)', maxHeight: '90vh', overflowY: 'auto' }}>
            <button
              onClick={() => setOutreachDraft(null)}
              aria-label="Close outreach draft"
              style={{ position: 'absolute', right: '1.5rem', top: '1.5rem', background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', minWidth: '44px', minHeight: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            >
              <X size={24} />
            </button>
            <h2 id="outreach-modal-title" style={{ marginBottom: '1.5rem', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <Mail color="var(--primary)" /> Outreach for {outreachDraft.leadName}
            </h2>
            <div style={{ background: 'var(--surface-muted)', padding: '1.5rem', borderRadius: '12px', color: 'var(--text-primary)', lineHeight: 1.6, whiteSpace: 'pre-wrap', marginBottom: '2rem', border: '1px solid var(--border-subtle)', fontSize: '0.95rem' }}>
              {outreachDraft.text}
            </div>

            {activeLead?.email_hook && (
              <div style={{ marginBottom: '1.5rem', padding: '1rem', background: 'rgba(165, 180, 252, 0.05)', borderRadius: '12px', border: '1px dashed rgba(165, 180, 252, 0.3)', position: 'relative' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.5rem' }}>
                  <div style={{ fontSize: '0.65rem', color: 'var(--primary-light)', textTransform: 'uppercase', fontWeight: 600 }}>Suggested Opening Hook</div>
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(activeLead.email_hook || '');
                      setCopiedHookType('email');
                      setTimeout(() => setCopiedHookType(null), 2000);
                    }}
                    style={{ background: 'none', border: 'none', color: 'var(--primary-light)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.7rem', minHeight: '32px', padding: '0.35rem 0.5rem' }}
                  >
                    {copiedHookType === 'email' ? <><Check size={12} /> Copied</> : <><Copy size={12} /> Copy Hook</>}
                  </button>
                </div>
                <p style={{ fontSize: '0.9rem', fontStyle: 'italic', margin: 0, color: 'var(--primary-light)' }}>&quot;{activeLead.email_hook}&quot;</p>
              </div>
            )}

            {linkedinDraft && (
              <div style={{ marginTop: '0', padding: '1.5rem', background: 'var(--linkedin-tint)', borderRadius: '12px', border: '1px solid rgba(10, 102, 194, 0.2)', marginBottom: '2rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem', color: 'var(--linkedin)' }}>
                  <Linkedin size={18} />
                  <h4 style={{ margin: 0, fontSize: '1rem', fontWeight: 700 }}>LinkedIn Connection Request</h4>
                </div>
                <p style={{ fontSize: '0.9rem', lineHeight: '1.6', color: 'var(--text-primary)', whiteSpace: 'pre-wrap', margin: 0 }}>
                  {linkedinDraft}
                </p>
                {activeLead?.linkedin_hook && (
                  <div style={{ marginTop: '1rem', padding: '0.75rem', background: 'var(--surface-muted)', borderRadius: '8px', borderLeft: '3px solid var(--linkedin)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.25rem' }}>
                      <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Personalized Connection Hook</div>
                      <button
                        onClick={() => {
                          navigator.clipboard.writeText(activeLead.linkedin_hook || '');
                          setCopiedHookType('linkedin');
                          setTimeout(() => setCopiedHookType(null), 2000);
                        }}
                        style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.65rem', minHeight: '32px', padding: '0.35rem 0.5rem' }}
                      >
                        {copiedHookType === 'linkedin' ? <><Check size={10} /> Copied</> : <><Copy size={10} /> Copy</>}
                      </button>
                    </div>
                    <p style={{ fontSize: '0.8rem', margin: 0, color: 'var(--text-primary)' }}>{activeLead.linkedin_hook}</p>
                  </div>
                )}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '1rem' }}>
                   <p style={{ fontSize: '0.7rem', color: 'var(--text-muted)', margin: 0 }}>
                    {linkedinDraft.length}/300 characters
                  </p>
                  <button 
                    className="btn-secondary" 
                    style={{ padding: '0.4rem 1rem', fontSize: '0.75rem', background: 'rgba(10, 102, 194, 0.2)', borderColor: 'var(--linkedin)', color: 'var(--text-white)' }}
                    onClick={() => {
                      navigator.clipboard.writeText(linkedinDraft);
                      showToast("LinkedIn draft copied!", 'success');
                    }}
                  >
                    Copy Invite
                  </button>
                </div>
              </div>
            )}
            <div style={{ display: 'flex', gap: '1rem' }}>
              <button className="btn-primary" style={{ flex: 1 }} onClick={() => {
                navigator.clipboard.writeText(outreachDraft.text);
                showToast('Draft copied to clipboard!', 'success');
              }}>
                Copy to Clipboard
              </button>
              <button className="btn-secondary" style={{ flex: 1 }} onClick={() => setOutreachDraft(null)}>
                Discard
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Discovery Modal */}
      {showDiscoveryModal && (
        <div ref={discoveryModalRef} role="dialog" aria-modal="true" aria-labelledby="discovery-modal-title" className="modal-backdrop">
          <div className="card" style={{ width: '100%', maxWidth: 'min(500px, 95vw)', padding: 'clamp(1.25rem, 4vw, 2rem)', position: 'relative', border: '1px solid var(--primary)', maxHeight: '90vh', overflowY: 'auto' }}>
            <button
              onClick={() => setShowDiscoveryModal(false)}
              aria-label="Close discovery"
              style={{ position: 'absolute', right: '1.5rem', top: '1.5rem', background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', minWidth: '44px', minHeight: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            >
              <X size={24} />
            </button>
            <h2 id="discovery-modal-title" style={{ marginBottom: '1.5rem', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <Globe color="var(--primary)" /> Lead Discovery Engine
            </h2>
            
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', marginBottom: '2rem' }}>
              <div>
                <label htmlFor="discovery-query" style={{ display: 'block', fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>What are you looking for?</label>
                <input
                  type="text"
                  id="discovery-query"
                  value={discoveryQuery}
                  onChange={(e) => setDiscoveryQuery(e.target.value)}
                  placeholder="e.g. Dental Clinics"
                  style={{ width: '100%', background: 'var(--surface-muted)', border: '1px solid var(--glass-border)', borderRadius: '12px', padding: '0.75rem 1rem', color: 'var(--text-white)' }}
                />
              </div>
              <div>
                <label htmlFor="discovery-location" style={{ display: 'block', fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>Location (Optional)</label>
                <input
                  type="text"
                  id="discovery-location"
                  value={discoveryLocation}
                  onChange={(e) => setDiscoveryLocation(e.target.value)}
                  placeholder="e.g. New York, NY"
                  style={{ width: '100%', background: 'var(--surface-muted)', border: '1px solid var(--glass-border)', borderRadius: '12px', padding: '0.75rem 1rem', color: 'var(--text-white)' }}
                />
              </div>
            </div>

            <div style={{ display: 'flex', gap: '1rem' }}>
              <button 
                className="btn-primary" 
                style={{ flex: 1, gap: '0.75rem', position: 'relative' }} 
                onClick={handleStartDiscovery}
                disabled={isDiscovering || !discoveryQuery}
              >
                {isDiscovering ? (
                  <>
                    <Loader2 className="animate-spin" size={18} />
                    <span>Mining {discoveryQuery}...</span>
                  </>
                ) : (
                  <>
                    <Play size={18} />
                    <span>Start Deep Search</span>
                  </>
                )}
              </button>
              <button 
                className="btn-secondary" 
                style={{ flex: 0.5 }} 
                onClick={() => setShowDiscoveryModal(false)}
                disabled={isDiscovering}
              >
                Cancel
              </button>
            </div>
            
            {(isDiscovering || (orchestratorJob && orchestratorJob.type === 'discovery')) && (
                <div style={{ marginTop: '1.5rem', padding: '1.5rem', background: orchestratorJob?.current_phase === 'CAPTCHA Required' ? 'var(--error-tint)' : 'var(--primary-tint-5)', borderRadius: '16px', border: orchestratorJob?.current_phase === 'CAPTCHA Required' ? '1px solid var(--error)' : '1px solid var(--primary)', animation: orchestratorJob?.status === 'running' ? 'pulse 2s infinite' : 'none' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1rem' }}>
                    {orchestratorJob?.current_phase === 'CAPTCHA Required' ? (
                      <AlertTriangle size={18} color="var(--error)" />
                    ) : (
                      <Loader2 className={orchestratorJob?.status === 'running' ? "animate-spin" : ""} size={18} color="var(--primary)" />
                    )}
                    <span style={{ fontSize: '0.9rem', fontWeight: 600, color: orchestratorJob?.current_phase === 'CAPTCHA Required' ? 'var(--error)' : 'var(--text-white)' }}>
                      {orchestratorJob?.current_phase || DISCOVERY_STEPS[discoveryStep]}
                    </span>
                  </div>
                  
                  {orchestratorJob?.current_phase === 'CAPTCHA Required' ? (
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                      Google search has blocked the automated scrapers. 
                      Please perform a manual search on the server or use a proxy.
                      <button 
                        className="btn-secondary" 
                        style={{ marginTop: '1rem', width: '100%', borderColor: 'var(--border-muted)', fontSize: '0.7rem' }}
                        onClick={() => { setIsDiscovering(false); setOrchestratorJob(null); }}
                      >
                        Acknowledge & Dismiss
                      </button>
                    </div>
                  ) : (
                    <>
                      <div style={{ width: '100%', height: '4px', background: 'var(--border-muted)', borderRadius: '2px', overflow: 'hidden' }}>
                        <div 
                          style={{ 
                            width: orchestratorJob?.status === 'completed' ? '100%' : `${((discoveryStep + 1) / DISCOVERY_STEPS.length) * 100}%`, 
                            height: '100%', 
                            background: 'var(--primary)',
                            transition: 'width 0.5s ease-out'
                          }} 
                        />
                      </div>
                      <p style={{ marginTop: '0.75rem', fontSize: '0.75rem', color: 'var(--text-muted)', textAlign: 'center', margin: 0 }}>
                        {orchestratorJob?.status === 'completed' ? 'Lead discovery complete!' : 'Tracking real-time discovery progress...'}
                      </p>
                    </>
                  )}
                </div>
            )}
            
            <p style={{ marginTop: '1.5rem', fontSize: '0.75rem', color: 'var(--text-dim)', textAlign: 'center' }}>
              We&apos;ll browse Google Maps and other sources to find leads. New results will appear in your inventory automatically.
            </p>
          </div>
        </div>
      )}
      {/* Settings Modal */}
      {showSettings && (
        <div ref={settingsModalRef} role="dialog" aria-modal="true" aria-labelledby="settings-modal-title" className="modal-backdrop" style={{ padding: '2rem' }}>
          <div className="card" style={{ width: '100%', maxWidth: '500px', padding: 'clamp(1.25rem, 4vw, 2.5rem)', position: 'relative', border: '1px solid var(--primary)' }}>
            <button
              onClick={() => setShowSettings(false)}
              aria-label="Close settings"
              style={{ position: 'absolute', right: '1.5rem', top: '1.5rem', background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', minWidth: '44px', minHeight: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            >
              <X size={24} />
            </button>
            <h2 id="settings-modal-title" style={{ marginBottom: '1.5rem', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <Settings color="var(--primary)" /> System Settings
            </h2>
            
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', marginBottom: '2rem' }}>
              <div style={{ padding: '1rem', background: 'var(--surface-elevated)', borderRadius: '12px', border: '1px solid var(--border-subtle)' }}>
                <h3 style={{ fontSize: '0.9rem', marginBottom: '0.5rem' }}>API Configuration</h3>
                <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Backend: <code>{API_BASE_URL}</code></p>
                <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Database: Supabase</p>
              </div>

              <div style={{ padding: '1rem', background: 'var(--surface-elevated)', borderRadius: '12px', border: '1px solid var(--border-subtle)' }}>
                <h3 style={{ fontSize: '0.9rem', marginBottom: '0.5rem' }}>Browser Persistence</h3>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-primary)' }}>Keep browser alive between audits</span>
                  <button
                    role="switch"
                    aria-checked={browserPersistence}
                    aria-label="Toggle browser persistence"
                    style={{
                      width: '44px',
                      height: '24px',
                      background: browserPersistence ? 'var(--primary)' : 'var(--border-muted)',
                      borderRadius: '12px',
                      position: 'relative',
                      cursor: 'pointer',
                      transition: 'background 0.2s',
                      border: 'none',
                      padding: 0
                    }}
                    onClick={() => setBrowserPersistence(!browserPersistence)}
                  >
                    <div style={{
                      width: '18px',
                      height: '18px',
                      background: 'white',
                      borderRadius: '50%',
                      position: 'absolute',
                      left: browserPersistence ? '23px' : '3px',
                      top: '3px',
                      transition: 'left 0.2s'
                    }} />
                  </button>
                </div>
              </div>

              <div style={{ padding: '1rem', background: 'var(--surface-elevated)', borderRadius: '12px', border: '1px solid var(--border-subtle)' }}>
                <h3 style={{ fontSize: '0.9rem', marginBottom: '1rem' }}>Data Export Management</h3>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: '0.75rem' }}>
                  <button 
                    className="btn-secondary" 
                    style={{ fontSize: '0.8rem', justifyContent: 'center' }}
                    onClick={async () => {
                      try {
                        const res = await apiFetch(`${API_BASE_URL}/export`);
                        const data = await res.json();
                        showToast(data.message || "Export generated!", 'success');
                      } catch {
                        showToast("Export generation failed.", 'error');
                      }
                    }}
                  >
                    Generate CSVs
                  </button>
                  <button 
                    className="btn-secondary" 
                    style={{ fontSize: '0.8rem', justifyContent: 'center', borderColor: 'var(--primary)', color: 'var(--primary)' }}
                    onClick={handleDownloadCsv}
                  >
                    Download Latest
                  </button>
                </div>
              </div>

              <div style={{ padding: '1rem', background: 'rgba(239, 68, 68, 0.05)', borderRadius: '12px', border: '1px solid var(--error-tint)' }}>
                <h4 style={{ fontSize: '0.9rem', color: 'var(--error)', marginBottom: '0.5rem' }}>Danger Zone</h4>
                <button className="btn-secondary" style={{ width: '100%', borderColor: 'var(--error)', color: 'var(--error)', fontSize: '0.8rem' }} onClick={handleClearLeads}>
                  Clear All Leads
                </button>
              </div>
            </div>

            <button className="btn-primary" style={{ width: '100%' }} onClick={() => setShowSettings(false)}>
              Save & Close
            </button>
          </div>
        </div>
      )}
      {/* Campaign Strategy Modal */}
      {campaign && (
        <div ref={campaignModalRef} role="dialog" aria-modal="true" aria-labelledby="campaign-modal-title" className="modal-backdrop" style={{ padding: '2rem' }}>
          <div className="card" style={{ width: '100%', maxWidth: 'min(900px, 95vw)', maxHeight: '90vh', overflow: 'hidden', display: 'flex', flexDirection: 'column', border: '1px solid var(--primary)', borderRadius: '24px' }}>
             <div style={{ padding: '1.5rem 2rem', borderBottom: '1px solid var(--border-muted)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'var(--surface-subtle)' }}>
               <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                 <div style={{ background: 'var(--primary)', borderRadius: '10px', padding: '0.6rem' }}>
                    <Zap size={20} color="white" />
                 </div>
                 <div>
                    <h2 id="campaign-modal-title" style={{ fontSize: '1.25rem', fontWeight: 700, margin: 0 }}>Campaign Outreach Strategy</h2>
                    <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', margin: 0 }}>Personalized drafts for {campaign.length} high-priority leads.</p>
                 </div>
               </div>
               <button
                 onClick={() => setCampaign(null)}
                 aria-label="Close campaign strategy"
                 style={{ background: 'var(--surface-muted)', border: 'none', borderRadius: '50%', width: '44px', height: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', cursor: 'pointer' }}
               >
                 <X size={20} />
               </button>
             </div>

             <div style={{ flex: 1, overflowY: 'auto', padding: 'clamp(1rem, 3vw, 2rem)', display: 'flex', flexDirection: 'column', gap: '2rem' }}>
                {campaign.map((item, idx) => (
                  <div key={idx} style={{ background: 'var(--surface-subtle)', border: '1px solid var(--border-subtle)', borderRadius: '16px', padding: '1.5rem', transition: 'all 0.2s' }}>
                     <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1rem' }}>
                        <div>
                           <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.2rem' }}>
                              <h4 style={{ margin: 0, fontSize: '1.1rem', color: 'var(--text-white)' }}>{item.company}</h4>
                              <span style={{ fontSize: '0.7rem', background: 'var(--primary-tint-10)', color: 'var(--primary-light)', padding: '0.1rem 0.5rem', borderRadius: '4px' }}>Lead {idx + 1}</span>
                           </div>
                           <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', margin: 0 }}>Greeting: <strong style={{ color: 'var(--text-primary)' }}>Hi {item.first_name || 'there'}</strong></p>
                        </div>
                        <button 
                          className="btn-secondary"
                          style={{ padding: '0.4rem 0.8rem', fontSize: '0.75rem', gap: '0.4rem' }}
                          onClick={() => {
                            navigator.clipboard.writeText(item.draft);
                            showToast(`Draft for ${item.company} copied!`, 'success');
                          }}
                        >
                          <Copy size={14} /> Copy Draft
                        </button>
                     </div>
                     <div style={{ background: 'var(--surface-muted)', padding: '1rem', borderRadius: '10px', fontSize: '0.9rem', color: 'var(--text-secondary)', lineHeight: 1.6, whiteSpace: 'pre-wrap', border: '1px solid var(--border-subtle)' }}>
                        {item.draft}
                     </div>
                  </div>
                ))}
             </div>

             <div style={{ padding: '1.5rem 2rem', background: 'var(--surface-subtle)', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end', gap: '1rem' }}>
                <button className="btn-secondary" onClick={() => setCampaign(null)}>Close Library</button>
                <button className="btn-primary" onClick={() => {
                   const allDrafts = campaign.map(c => `PROSPECT: ${c.company}\nDRAFT:\n${c.draft}\n\n`).join('-------------------\n');
                   navigator.clipboard.writeText(allDrafts);
                   showToast("All campaign drafts copied to clipboard!", 'success');
                }}>
                   <Copy size={18} /> Copy All Drafts
                </button>
             </div>
          </div>
        </div>
      )}
    </div>
  );
}
