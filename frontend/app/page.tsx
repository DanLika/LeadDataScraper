'use client';

import { useCallback, useState, useEffect, useMemo, useRef, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import dynamic from 'next/dynamic';
import { useFocusTrap } from '@/app/hooks/useFocusTrap';
import { restoreFocus, BURGER_SELECTOR } from '@/app/hooks/useEscape';
// Row-level icons (Globe, Phone, Crosshair, Music, Pin, Facebook, Instagram,
// AlertCircle, etc.) all moved into LeadTable.tsx with the JSX they belong
// to. Page-level chrome still uses Upload/Mail/Shield/Settings/etc.
import {
  Upload, Globe, Mail, Shield,
  Settings, AlertTriangle,
  Download, FileDown, Crosshair,
  Loader2, Play, RefreshCw, X, Zap,
  Copy, Check, Menu,
} from 'lucide-react';
import { Linkedin } from './components/BrandIcons';
import Sidebar from './components/Sidebar';
import StatsCards from './components/StatsCards';
import FilterBar, { DEFAULT_SORT, type SortKey } from './components/FilterBar';
import { API_BASE_URL, apiFetch } from '@/app/lib/apiConfig';
import { ensureProtocol } from '@/app/lib/url.mjs';

// Heavy components are lazy-loaded so the dashboard's initial JS payload
// stays under the perf budget. Recharts (HealthChart) and the AI chat
// runtime both ship as separate chunks fetched only when those islands
// actually render. ssr:false skips RSC pre-rendering of these client
// islands — saves both bytes and a server-side React tree pass; the
// placeholder reserves height to prevent layout shift while the chunk
// is in flight.
const HealthChart = dynamic(() => import('./components/HealthChart'), {
  ssr: false,
  loading: () => <div className="card card-no-hover" style={{ minHeight: 280 }} aria-hidden="true" />,
});
const AIChat = dynamic(() => import('./components/AIChat'), { ssr: false });
// LeadTable is the heaviest single component (virtualizer + 200 LOC of
// row cell rendering). Lazy-loading here is dual-purpose: keep initial
// JS small AND defer the @tanstack/react-virtual import (~5KB gz) so
// it's only fetched once the user is on the dashboard. ssr:false because
// the table only renders meaningful content after the /leads fetch
// completes — there's nothing useful to pre-render.
const LeadTable = dynamic(() => import('./components/LeadTable'), {
  ssr: false,
  loading: () => (
    <div style={{ padding: '4rem', textAlign: 'center', color: 'var(--text-dim)' }}>
      Loading inventory…
    </div>
  ),
});

import type { Lead } from './types/lead';

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

// cleanMarkdown + CollapsibleText were only ever called from the lead
// inventory table cells; both moved into LeadTable.tsx alongside the
// JSX that uses them. Re-add here only if a non-table caller appears.

// Suspense wrapper so `useSearchParams()` below doesn't trip Next 16's
// "missing-suspense-with-csr-bailout" prerender check during `next build`.
// The dashboard has no meaningful static shell (everything depends on the
// authed session + live data), so fallback={null} is appropriate.
export default function Dashboard() {
  return (
    <Suspense fallback={null}>
      <DashboardInner />
    </Suspense>
  );
}

function DashboardInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const tDash = useTranslations('dashboard');
  const tCommon = useTranslations('common');
  const tOutreach = useTranslations('modals.outreach');
  const tDiscovery = useTranslations('modals.discovery');
  const tSettings = useTranslations('modals.settings');
  const tCampaign = useTranslations('modals.campaign');
  const [leads, setLeads] = useState<Lead[]>([]);
  // DB-wide total from /stats, separate from the paginated `leads` array.
  // Null until the first /stats response lands; StatsCards then displays it
  // on the TOTAL LEADS card instead of the loaded-slice count.
  const [totalLeads, setTotalLeads] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [exportingKind, setExportingKind] = useState<null | 'full' | 'outreach'>(null);
  const [exportStage, setExportStage] = useState<'connecting' | 'fetching' | null>(null);
  const [exportBytes, setExportBytes] = useState(0);
  const [outreachDraft, setOutreachDraft] = useState<{ text: string, leadName: string, subject?: string, leadEmail?: string } | null>(null);
  const [linkedinDraft, setLinkedinDraft] = useState<string>('');
  const [isDrafting, setIsDrafting] = useState(false);
  const [insights, setInsights] = useState<Insights | null>(null);
  const [fetchingInsights, setFetchingInsights] = useState(false);
  const [auditStatus, setAuditStatus] = useState<AuditStatusInfo | null>(null);
  const [showDiscoveryModal, setShowDiscoveryModal] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [demoRemoveOpen, setDemoRemoveOpen] = useState(false);
  const [demoRemoveText, setDemoRemoveText] = useState('');
  const [isRemovingDemo, setIsRemovingDemo] = useState(false);

  // Phase 13.3 — "Show demo data" toggle. Defaults OFF so the operator's
  // first impression is real-lead-only. Persisted in localStorage as
  // `lds-include-demo` so a refresh keeps the state; reading happens in
  // a mount effect (not lazy initialState) so SSR + hydration agree on
  // `false`, then client upgrades to the stored value.
  const [showDemo, setShowDemo] = useState<boolean>(false);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (window.localStorage.getItem('lds-include-demo') === '1') setShowDemo(true);
  }, []);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (showDemo) window.localStorage.setItem('lds-include-demo', '1');
    else window.localStorage.removeItem('lds-include-demo');
  }, [showDemo]);

  // Auto-open Settings / Discovery / set view when arriving from /insights or
  // /campaigns via ?openSettings=1 / ?openDiscovery=1 / ?view=audited|high-risk.
  // After consuming, strip the query so refresh doesn't re-trigger. Sidebar
  // lives on every page but its state only exists on Dashboard — this bridges.
  useEffect(() => {
    const openSettings = searchParams?.get('openSettings') === '1';
    const openDiscovery = searchParams?.get('openDiscovery') === '1';
    const viewParam = searchParams?.get('view');
    const searchParam = searchParams?.get('search');
    if (openSettings || openDiscovery || viewParam || searchParam) {
      if (openSettings) setShowSettings(true);
      if (openDiscovery) setShowDiscoveryModal(true);
      if (viewParam === 'audited' || viewParam === 'high-risk') setView(viewParam);
      if (searchParam) setSearchTerm(searchParam);
      // Preserve the search as ?q= so the URL-state sync below sees a
      // consistent vocabulary. Otherwise replace('/') strips searchTerm
      // on the very next read tick.
      const dest = searchParam ? `/?q=${encodeURIComponent(searchParam)}` : '/';
      router.replace(dest, { scroll: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const settingsModalRef = useRef<HTMLDivElement>(null);
  const discoveryModalRef = useRef<HTMLDivElement>(null);
  const outreachModalRef = useRef<HTMLDivElement>(null);
  const campaignModalRef = useRef<HTMLDivElement>(null);
  const [discoveryQuery, setDiscoveryQuery] = useState('');
  const [discoveryLocation, setDiscoveryLocation] = useState('');
  const [isDiscovering, setIsDiscovering] = useState(false);
  const [isGeneratingCsv, setIsGeneratingCsv] = useState(false);
  const [isExportingMyData, setIsExportingMyData] = useState(false);
  const [orchestratorJob, setOrchestratorJob] = useState<OrchestratorJob | null>(null);
  const [, setProcessingAi] = useState(false);
  const [processingLeads, setProcessingLeads] = useState<Record<string, boolean>>({});
  const [activeLead, setActiveLead] = useState<Lead | null>(null);
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
  const [sortKey, setSortKey] = useState<SortKey>(DEFAULT_SORT);
  const [copiedHookType, setCopiedHookType] = useState<'email' | 'linkedin' | null>(null);
  const [copiedAction, setCopiedAction] = useState<'body' | 'subject' | 'invite' | null>(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [discoveryStep, setDiscoveryStep] = useState(0);
  const [toasts, setToasts] = useState<Array<{ id: number; message: string; type: 'success' | 'error' | 'info' }>>([]);

  const showToast = useCallback((message: string, type: 'success' | 'error' | 'info' = 'info') => {
    // Date.now() collides when two toasts fire in the same millisecond
    // (e.g. rapid 5x click). React then warns about duplicate keys and may
    // drop one toast. Append a counter so each toast id is unique even in
    // the same ms.
    const id = Date.now() * 1000 + Math.floor(Math.random() * 1000);
    setToasts(prev => [...prev, { id, message, type }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 3500);
  }, []);

  // ESC key handler for all modals + mobile drawer
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (campaign) setCampaign(null);
        else if (outreachDraft) setOutreachDraft(null);
        else if (showSettings) setShowSettings(false);
        else if (showDiscoveryModal && !isDiscovering) setShowDiscoveryModal(false);
        else if (isSidebarOpen) {
          setIsSidebarOpen(false);
          restoreFocus(BURGER_SELECTOR);
        }
      }
    };
    document.addEventListener('keydown', handleEsc);
    return () => document.removeEventListener('keydown', handleEsc);
  }, [campaign, outreachDraft, showSettings, showDiscoveryModal, isDiscovering, isSidebarOpen]);

  // Cursor pagination state. `nextCursor` is the opaque token returned
  // by the backend for the next page; null means we've reached the tail.
  // `hasMore` is the authoritative end-of-stream signal — surface it on
  // the Load-more button so the user can tell "data is loading" from
  // "you've seen everything".
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState<boolean>(false);
  const [isLoadingMore, setIsLoadingMore] = useState<boolean>(false);

  const fetchStats = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await apiFetch(`${API_BASE_URL}/stats`, { signal });
      if (!response.ok) return;
      const data = await response.json();
      if (typeof data?.total_leads === 'number') setTotalLeads(data.total_leads);
    } catch (err) {
      if (signal?.aborted) return;
      console.error('Stats fetch failed:', err);
    }
  }, []);

  const fetchLeads = useCallback(async (signal?: AbortSignal) => {
    try {
      // Refresh (not append): drop any cursor and request the first page.
      // The 15s polling loop calls this — it must return to page 1 so
      // newly-discovered leads at the top of created_at DESC become
      // visible. The Load-more button uses a separate handler that
      // *appends* the next page.
      const url = `${API_BASE_URL}/leads?limit=50${showDemo ? '&include_demo=true' : ''}`;
      const response = await apiFetch(url, { signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setLeads(data.leads || []);
      setNextCursor(typeof data.next_cursor === 'string' ? data.next_cursor : null);
      setHasMore(!!data.has_more);
    } catch (err) {
      // A fetch cancelled by effect-cleanup / navigation is benign. WebKit
      // reports it as a bare `TypeError: Load failed` (no AbortError), so
      // we discriminate on `signal.aborted` — whatever the error type, if
      // WE aborted it, don't spam the console.
      if (signal?.aborted) return;
      console.error('Error fetching leads:', err);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [showDemo]);

  const loadMoreLeads = useCallback(async () => {
    if (!nextCursor || isLoadingMore) return;
    setIsLoadingMore(true);
    try {
      // encodeURIComponent the cursor — base64url is mostly URL-safe but
      // belt-and-braces for the `=` padding character.
      const response = await apiFetch(
        `${API_BASE_URL}/leads?limit=50&cursor=${encodeURIComponent(nextCursor)}${showDemo ? '&include_demo=true' : ''}`
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const page: Lead[] = data.leads || [];
      // Dedup by unique_key in case the polling refresh raced an append.
      setLeads(prev => {
        const seen = new Set(prev.map(l => l.unique_key));
        return [...prev, ...page.filter(l => !seen.has(l.unique_key))];
      });
      setNextCursor(typeof data.next_cursor === 'string' ? data.next_cursor : null);
      setHasMore(!!data.has_more);
    } catch (err) {
      console.error('Load-more failed:', err);
      showToast('Failed to load more leads — backend unreachable.', 'error');
    } finally {
      setIsLoadingMore(false);
    }
  }, [nextCursor, isLoadingMore, showToast, showDemo]);

  const fetchInsights = useCallback(async (signal?: AbortSignal) => {
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
    // AbortController ties the in-flight fetches to this effect's lifetime.
    // React 19 StrictMode double-invokes effects in dev (mount → cleanup →
    // remount); without the abort the first run's fetches dangle and WebKit
    // surfaces the browser-cancelled request as `TypeError: Load failed`.
    const controller = new AbortController();
    fetchLeads(controller.signal);
    fetchStats(controller.signal);
    fetchInsights(controller.signal);

    // Poll the backend for fresh leads instead of subscribing via the browser
    // Supabase client. Supabase Realtime requires anon access to the table,
    // which is intentionally disabled by RLS — backend is now the only reader.
    const interval = setInterval(() => {
      fetchLeads(controller.signal);
      fetchStats(controller.signal);
    }, 15000);

    return () => {
      controller.abort();
      clearInterval(interval);
    };
  }, [fetchLeads, fetchStats, fetchInsights]);

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

  // Cross-tab job visibility — adopts a running orchestration job started
  // in another tab so the operator's second tab also shows the spinner +
  // progress instead of looking idle.
  //
  // Cadence: starts at 5s, exponentially backs off to 10s then 30s once the
  // tab has been idle for several consecutive ticks (no job adopted, no
  // visibility change). Resets to 5s the moment the tab regains focus, on
  // hard backend errors (so a transient blip doesn't push us straight to
  // 30s), or once a job is adopted (next useEffect takes over the polling).
  // Phase 15 observation: idle dashboards were issuing ~12 /orchestrator
  // /active calls per minute (one fixed 5s setInterval × visible tab).
  // Backoff brings that to ~2/min once the tab has been idle for ~30s.
  //
  // Visibility pause: when the tab is hidden, we skip the actual fetch but
  // keep the setTimeout running so the loop is alive — the next tick is
  // also short-circuited until visibility returns, at which point we fire
  // immediately and reset the backoff.
  useEffect(() => {
    if (orchestratorJob) return;
    let cancelled = false;
    let idleTicks = 0;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;

    const POLL_DELAYS_MS = [5_000, 10_000, 30_000] as const;
    const computeDelay = (idle: number): number => {
      if (idle < 2) return POLL_DELAYS_MS[0];
      if (idle < 4) return POLL_DELAYS_MS[1];
      return POLL_DELAYS_MS[2];
    };

    const schedule = () => {
      if (cancelled) return;
      if (timeoutId !== null) clearTimeout(timeoutId);
      timeoutId = setTimeout(tick, computeDelay(idleTicks));
    };

    const tick = async () => {
      if (cancelled) return;
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') {
        // Skip the fetch but keep the chain alive — visibility return
        // re-fires immediately via the listener below.
        schedule();
        return;
      }
      try {
        const res = await apiFetch(`${API_BASE_URL}/orchestrator/active`);
        if (cancelled) return;
        if (!res.ok) {
          // Transient HTTP error — don't widen the backoff window on it;
          // keep the current `idleTicks` and re-schedule.
          schedule();
          return;
        }
        const data = await res.json();
        if (cancelled) return;
        if (data?.job && (data.job.status === 'running' || data.job.status === 'starting')) {
          // Job adopted — the effect's deps change next render and this
          // poller stops cleanly via the return below.
          setOrchestratorJob(data.job);
          idleTicks = 0;
          return;
        }
        idleTicks += 1;
      } catch {
        /* network blip — re-schedule, don't widen backoff */
      }
      schedule();
    };

    const onVisibility = () => {
      if (document.visibilityState === 'visible') {
        idleTicks = 0; // returning operator gets fast feedback
        void tick();
      }
    };

    void tick();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      cancelled = true;
      if (timeoutId !== null) clearTimeout(timeoutId);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [orchestratorJob]);

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
      // /execute uses extra='forbid' on its Pydantic model — only task + params
      // are accepted. The `reasoning` field added by the AI router on /ask
      // would otherwise trigger HTTP 422. Strip it before forwarding.
      const cleanPlan = { task: plan.task, params: plan.params };
      const response = await apiFetch(`${API_BASE_URL}/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cleanPlan),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const msg = Array.isArray(data?.detail)
          ? data.detail.map((d: { msg?: string }) => d?.msg).filter(Boolean).join('; ')
          : (data?.detail || data?.error || `Execute failed (HTTP ${response.status})`);
        showToast(msg, 'error');
        throw new Error(msg);
      }
      
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
          setOutreachDraft({
            text: data.result.draft,
            leadName: data.result.lead_name || 'Prospect',
            subject: data.result.subject || '',
            leadEmail: data.result.lead_email || '',
          });
        } else if (plan.task === 'LINKEDIN_DRAFT') {
          setLinkedinDraft(data.result.draft);
          setOutreachDraft({
            text: data.result.draft,
            leadName: data.result.recipient || 'Prospect',
            subject: '',
            leadEmail: '',
          });
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

  const ingestFile = async (file: File) => {
    if (!ALLOWED_UPLOAD_TYPES.includes(file.type) && !file.name.endsWith('.csv')) {
      const ext = (file.name.split('.').pop() || file.type || 'unknown').toUpperCase();
      showToast(`Only CSV files are accepted (got ${ext}).`, 'error');
      return;
    }
    if (file.size > MAX_UPLOAD_SIZE) {
      showToast('File is too large. Maximum size is 10MB.', 'error');
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
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        showToast(data.detail || data.error || `Upload failed (HTTP ${response.status})`, 'error');
        return;
      }
      // Backend processes upload asynchronously and returns
      // `{message: "Leads are being imported in the background."}` — surface that
      // exact text so user knows rows won't appear immediately.
      const msg = data.message
        || (data.inserted != null
            ? `${data.inserted} lead${data.inserted === 1 ? '' : 's'} imported.`
            : 'CSV uploaded — processing in the background.');
      showToast(msg, 'success');
      // Schedule a follow-up refresh in 5s to let background task land
      setTimeout(() => { fetchLeads(); }, 5000);
    } catch (err) {
      console.error('Upload failed:', err);
      showToast('Upload failed — backend unreachable.', 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) await ingestFile(file);
    e.target.value = '';
  };

  // Drag-drop ingest. dragenter/leave fire on every child crossing, so a
  // ref-counter tracks net depth and isDragging follows that — without it the
  // overlay flickers as the cursor crosses internal elements (e.g. the
  // sidebar). Files-only guard: a text drag (e.g. selection) shouldn't open
  // the overlay.
  const [isDragging, setIsDragging] = useState(false);
  const dragCounterRef = useRef(0);

  const isFileDrag = (dt: DataTransfer | null) =>
    !!dt && Array.from(dt.types || []).includes('Files');

  const onDashboardDragEnter = (e: React.DragEvent<HTMLDivElement>) => {
    if (!isFileDrag(e.dataTransfer)) return;
    e.preventDefault();
    dragCounterRef.current += 1;
    if (dragCounterRef.current === 1) setIsDragging(true);
  };
  const onDashboardDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    if (!isFileDrag(e.dataTransfer)) return;
    // Must preventDefault to make the drop event fire on this element.
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
  };
  const onDashboardDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    if (!isFileDrag(e.dataTransfer)) return;
    e.preventDefault();
    dragCounterRef.current = Math.max(0, dragCounterRef.current - 1);
    if (dragCounterRef.current === 0) setIsDragging(false);
  };
  const onDashboardDrop = async (e: React.DragEvent<HTMLDivElement>) => {
    if (!isFileDrag(e.dataTransfer)) return;
    e.preventDefault();
    dragCounterRef.current = 0;
    setIsDragging(false);
    const files = Array.from(e.dataTransfer?.files || []);
    if (files.length === 0) return;
    if (loading) {
      showToast('Upload already in progress — wait for it to finish.', 'error');
      return;
    }
    if (files.length > 1) {
      showToast(`Only the first file was imported (${files.length - 1} other${files.length === 2 ? '' : 's'} ignored).`, 'info');
    }
    await ingestFile(files[0]);
  };

  const processLead = async (uniqueKey: string) => {
    setProcessingLeads(prev => ({ ...prev, [uniqueKey]: true }));
    try {
      const resp = await apiFetch(`${API_BASE_URL}/process-lead`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ unique_key: uniqueKey }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        showToast(data.detail || data.error || `Re-audit failed (HTTP ${resp.status})`, 'error');
        return;
      }
      if (data.job_id) {
        setOrchestratorJob({ id: data.job_id, status: 'starting', processed_count: 0, total_count: 1, type: 'audit' });
        showToast('Re-audit queued.', 'success');
      } else {
        showToast('Re-audit accepted (no job ID).', 'info');
      }
    } catch (err) {
      console.error('Process lead failed:', err);
      showToast('Re-audit failed — backend unreachable.', 'error');
    } finally {
      setProcessingLeads(prev => ({ ...prev, [uniqueKey]: false }));
    }
  };

  const processAll = async () => {
    if (leads.length === 0) {
      showToast('No leads to audit. Import a CSV first.', 'info');
      return;
    }
    if (!confirm(`Run SEO audit on ${leads.length} leads? This may take several minutes and hit Google rate limits.`)) return;
    setLoading(true);
    try {
      const resp = await apiFetch(`${API_BASE_URL}/orchestrator/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: {}, tasks: ['audit'] }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        showToast(data.detail || data.error || `Audit start failed (HTTP ${resp.status})`, 'error');
        return;
      }
      if (data.job_id) {
        setOrchestratorJob({ id: data.job_id, status: 'starting', processed_count: 0, total_count: 0, type: 'audit' });
        showToast(`Audit started — job ${String(data.job_id).slice(0, 8)}…`, 'success');
      } else {
        showToast('Audit accepted but no job ID returned.', 'info');
      }
    } catch (err) {
      console.error('Process all failed:', err);
      showToast('Audit failed — backend unreachable.', 'error');
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
      if (data.draft) setOutreachDraft({
        text: data.draft,
        leadName: lead.company_name || lead.name || 'Prospect',
        subject: data.subject || '',
        leadEmail: data.lead_email || lead.email || '',
      });
      
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
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        showToast(data.detail || data.error || `Hunt failed (HTTP ${resp.status})`, 'error');
        return;
      }
      if (data.job_id) {
        setOrchestratorJob({ id: data.job_id, status: 'starting', processed_count: 0, total_count: 1, type: 'hunt' });
        showToast('Deep hunt queued.', 'success');
      } else {
        showToast('Hunt accepted (no job ID).', 'info');
      }
    } catch (err) {
      console.error('Deep hunt failed:', err);
      showToast('Hunt failed — backend unreachable.', 'error');
    } finally {
      setProcessingLeads(prev => ({ ...prev, [uniqueKey]: false }));
    }
  };

  const handleDeepHuntAll = async () => {
    if (leads.length === 0) {
      showToast('No leads to hunt. Import a CSV first.', 'info');
      return;
    }
    if (!confirm(`Launch Deep Digital Hunt on ${leads.length} leads? Playwright will scrape each website (slow + bandwidth-heavy).`)) return;
    setLoading(true);
    try {
      const resp = await apiFetch(`${API_BASE_URL}/orchestrator/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: {}, tasks: ['hunt'] }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        showToast(data.detail || data.error || `Hunt start failed (HTTP ${resp.status})`, 'error');
        return;
      }
      if (data.job_id) {
        setOrchestratorJob({ id: data.job_id, status: 'starting', processed_count: 0, total_count: 0, type: 'hunt' });
        showToast(`Hunt started — job ${String(data.job_id).slice(0, 8)}…`, 'success');
      } else {
        showToast('Hunt accepted but no job ID returned.', 'info');
      }
    } catch (err) {
      console.error('Deep hunt all failed:', err);
      showToast('Hunt failed — backend unreachable.', 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleStartDiscovery = async () => {
    if (!discoveryQuery.trim() || !discoveryLocation.trim()) return;
    setIsDiscovering(true);
    try {
      const response = await apiFetch(`${API_BASE_URL}/discovery/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: discoveryQuery, location: discoveryLocation }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        showToast(data.detail || data.error || `Discovery failed (HTTP ${response.status})`, 'error');
        return;
      }
      if (data.job_id) {
        setOrchestratorJob({
          id: data.job_id,
          status: 'starting',
          current_phase: 'Initializing...',
          type: 'discovery',
          processed_count: 0,
          total_count: 0
        });
        setShowDiscoveryModal(false);
        showToast(`Discovery started — job ${String(data.job_id).slice(0, 8)}…`, 'success');
      } else {
        showToast('Discovery accepted but no job ID returned — check backend logs.', 'info');
      }
    } catch (err) {
      console.error('Discovery failed:', err);
      showToast('Discovery failed — backend unreachable.', 'error');
    } finally {
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
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        showToast(data.detail || data.error || `Harvest failed (HTTP ${resp.status})`, 'error');
        return;
      }
      if (data.job_id) {
        setOrchestratorJob({ id: data.job_id, status: 'starting', processed_count: 0, total_count: 1, type: 'enrich' });
        showToast('Contact harvest queued.', 'success');
      } else {
        showToast('Harvest accepted (no job ID).', 'info');
      }
    } catch (err) {
      console.error('Enrichment failed:', err);
      showToast('Harvest failed — backend unreachable.', 'error');
    } finally {
      setProcessingLeads(prev => ({ ...prev, [uniqueKey]: false }));
    }
  };

  const handleRemoveDemo = async () => {
    if (demoRemoveText !== 'REMOVE DEMO' || isRemovingDemo) return;
    setIsRemovingDemo(true);
    try {
      const res = await apiFetch(`${API_BASE_URL}/leads/demo`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmation: 'REMOVE DEMO' }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(data.detail || data.error || `Demo removal failed (HTTP ${res.status})`, 'error');
        return;
      }
      const leadsDeleted = data.leads_deleted ?? 0;
      const msgsDeleted = data.messages_deleted ?? 0;
      showToast(
        leadsDeleted
          ? `Removed ${leadsDeleted} demo lead${leadsDeleted === 1 ? '' : 's'}${msgsDeleted ? ` + ${msgsDeleted} message${msgsDeleted === 1 ? '' : 's'}` : ''}.`
          : 'No demo data found to remove.',
        leadsDeleted ? 'success' : 'info'
      );
      setDemoRemoveOpen(false);
      setDemoRemoveText('');
      fetchLeads();
    } catch (err) {
      console.error('Demo removal failed:', err);
      showToast('Demo removal failed — backend unreachable.', 'error');
    } finally {
      setIsRemovingDemo(false);
    }
  };

  const handleClearLeads = async () => {
    if (!confirm("Are you SURE you want to clear all leads? This cannot be undone.")) return;
    setLoading(true);
    try {
      const res = await apiFetch(`${API_BASE_URL}/leads/clear`, { method: 'DELETE' });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        showToast(body.error || body.detail || `Clear failed (HTTP ${res.status})`, 'error');
        return;
      }
      setLeads([]);
      setInsights(null);
      showToast("All leads have been cleared.", 'success');
      setShowSettings(false);
    } catch (err) {
      console.error('Clear leads failed:', err);
      showToast('Clear failed — backend unreachable.', 'error');
    } finally {
      setLoading(false);
    }
  };

  const startMassivePipeline = async () => {
    if (leads.length === 0) {
      showToast('No leads to orchestrate. Import a CSV first.', 'info');
      return;
    }
    if (!confirm(`Run FULL pipeline (audit + enrich + hunt) on ${leads.length} leads? This is the most expensive operation — multi-minute, multi-source scrape.`)) return;
    setLoading(true);
    try {
      const resp = await apiFetch(`${API_BASE_URL}/orchestrator/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: {}, tasks: ['audit', 'enrich', 'hunt'] }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        showToast(data.detail || data.error || `Orchestrator failed (HTTP ${resp.status})`, 'error');
        return;
      }
      if (data.job_id) {
        setOrchestratorJob({ id: data.job_id, status: 'starting', processed_count: 0, total_count: 0, type: 'massive' });
        showToast(`Pipeline started — job ${String(data.job_id).slice(0, 8)}…`, 'success');
      } else {
        showToast('Pipeline accepted but no job ID returned.', 'info');
      }
    } catch (err) {
      console.error('Failed to start massive pipeline:', err);
      showToast('Pipeline failed — backend unreachable.', 'error');
    } finally {
      setLoading(false);
    }
  };

  const stopOrchestratorJob = async () => {
    if (!orchestratorJob?.id) return;
    try {
      const resp = await apiFetch(`${API_BASE_URL}/orchestrator/stop/${orchestratorJob.id}`, { method: 'POST' });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        showToast(data.detail || data.error || `Stop failed (HTTP ${resp.status})`, 'error');
        return;
      }
      setOrchestratorJob({ ...orchestratorJob, status: 'stopped' });
      showToast('Job stopped.', 'success');
    } catch (err) {
      console.error('Stop job failed:', err);
      showToast('Stop failed — backend unreachable.', 'error');
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
  const triggerCsvDownload = async (
    kind: 'full' | 'outreach',
    path: string,
    filename: string,
    emptyMessage: string,
  ) => {
    if (exportingKind !== null) return;
    setExportingKind(kind);
    setExportStage('connecting');
    setExportBytes(0);
    try {
      const res = await apiFetch(`${API_BASE_URL}${path}`, { cache: 'no-store' });
      const ctype = res.headers.get('content-type') || '';
      if (!res.ok || !ctype.includes('csv')) {
        let detail = emptyMessage;
        if (ctype.includes('json')) {
          const body = await res.json().catch(() => null);
          detail = body?.error || body?.detail || emptyMessage;
        }
        showToast(detail, 'error');
        return;
      }
      // Read the stream chunk-by-chunk so the button label can reflect live
      // bytes-received instead of freezing on "Preparing…" for the full
      // server-side keyset walk. `await res.blob()` would not resolve until
      // the entire CSV streamed; the user reads that as "broken".
      const reader = res.body?.getReader();
      let blob: Blob;
      if (reader) {
        setExportStage('fetching');
        const chunks: BlobPart[] = [];
        let bytes = 0;
        let lastTick = 0;
        // setState throttled to ~10Hz so paint keeps up on long streams.
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          if (value) {
            chunks.push(value);
            bytes += value.length;
            const now = performance.now();
            if (now - lastTick > 100) {
              lastTick = now;
              setExportBytes(bytes);
            }
          }
        }
        setExportBytes(bytes);
        blob = new Blob(chunks, { type: 'text/csv' });
      } else {
        blob = await res.blob();
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      showToast(`${filename} downloaded.`, 'success');
    } catch (err) {
      console.error('Download failed:', err);
      showToast('Download failed — try again.', 'error');
    } finally {
      setExportingKind(null);
      setExportStage(null);
      setExportBytes(0);
    }
  };

  const handleDownloadCsv = () => triggerCsvDownload(
    'full',
    '/export/download',
    `leads-export-${new Date().toISOString().slice(0,10)}.csv`,
    'No leads to export yet.'
  );
  const handleDownloadOutreachCsv = () => triggerCsvDownload(
    'outreach',
    '/export/outreach',
    `outreach-export-${new Date().toISOString().slice(0,10)}.csv`,
    'No outreach files generated yet — draft outreach for leads first.'
  );

  const exportLabel = (idle: string): string => {
    if (exportStage === 'connecting') return 'Connecting…';
    if (exportStage === 'fetching') {
      if (exportBytes === 0) return 'Fetching…';
      const kb = exportBytes / 1024;
      return kb >= 1024
        ? `Fetching ${(kb / 1024).toFixed(1)} MB`
        : `Fetching ${Math.round(kb)} KB`;
    }
    return idle;
  };

  const filteredLeads = useMemo(() => {
    const matched = leads.filter((lead: Lead) => {
      const matchesSearch = (lead.company_name || lead.name || '').toLowerCase().includes(searchTerm.toLowerCase()) ||
                           (lead.website || '').toLowerCase().includes(searchTerm.toLowerCase());

      const matchesSegment = filterSegment === 'all' || lead.segment === filterSegment;
      const matchesScore = (lead.outreach_score || lead.audit_results?.score || 0) >= filterMinScore;
      const matchesAuditStatus = filterAuditStatus === 'all' || lead.audit_status === filterAuditStatus;

      const matchesAllFilters = matchesSegment && matchesScore && matchesAuditStatus;

      if (view === 'audited') return matchesSearch && lead.audit_status === 'Completed' && matchesAllFilters;
      if (view === 'high-risk') return matchesSearch && ((lead.audit_results?.score ?? 100) < 50 || lead.high_risk_flag || lead.audit_results?.high_risk_flag) && matchesAllFilters;
      return matchesSearch && matchesAllFilters;
    });
    // Sort. Null/undefined values sort last (worst rank) in both directions so
    // un-audited rows don't poison the top of a seo_score-desc view.
    const scoreOf = (l: Lead, key: 'seo' | 'outreach'): number | null => {
      if (key === 'seo') {
        const v = l.seo_score ?? l.audit_results?.score;
        return v == null ? null : Number(v);
      }
      const v = l.outreach_score;
      return v == null ? null : Number(v);
    };
    const cmpNullable = (a: number | null, b: number | null, desc: boolean) => {
      if (a == null && b == null) return 0;
      if (a == null) return 1;
      if (b == null) return -1;
      return desc ? b - a : a - b;
    };
    const sorted = matched.slice();
    sorted.sort((a, b) => {
      switch (sortKey) {
        case 'seo_score_desc':
          return cmpNullable(scoreOf(a, 'seo'), scoreOf(b, 'seo'), true);
        case 'seo_score_asc':
          return cmpNullable(scoreOf(a, 'seo'), scoreOf(b, 'seo'), false);
        case 'outreach_score_desc':
          return cmpNullable(scoreOf(a, 'outreach'), scoreOf(b, 'outreach'), true);
        case 'name_asc':
          return (a.company_name || a.name || '').localeCompare(b.company_name || b.name || '');
        case 'name_desc':
          return (b.company_name || b.name || '').localeCompare(a.company_name || a.name || '');
        case 'created_at_desc':
        default:
          return (b.created_at || '').localeCompare(a.created_at || '');
      }
    });
    return sorted;
  }, [leads, searchTerm, filterSegment, filterMinScore, filterAuditStatus, view, sortKey]);

  const segmentOptions = useMemo(() =>
    Array.from(new Set(leads.map((l: Lead) => l.segment).filter(Boolean))),
  [leads]);

  const hasActiveFilters = useMemo(
    () => filterSegment !== 'all' || filterAuditStatus !== 'all' || filterMinScore > 0 || searchTerm.length > 0 || sortKey !== DEFAULT_SORT,
    [filterSegment, filterAuditStatus, filterMinScore, searchTerm, sortKey],
  );

  const clearFilters = useCallback(() => {
    setFilterSegment('all');
    setFilterAuditStatus('all');
    setFilterMinScore(0);
    setSearchTerm('');
    setSortKey(DEFAULT_SORT);
    // Strip URL params immediately. The bidirectional URL-sync write-effect
    // below normally handles this, but the read-effect's `filterReadInFlightRef`
    // suppression can occasionally race with the user clicking Clear right
    // after a deep-link arrives (Phase 15 finding #2: state cleared, URL
    // kept stale `?status=Pending&q=pacific&sort=...` and a subsequent reload
    // re-applied the filters). Calling `router.replace('/')` here bypasses
    // the race; the write-effect's diff check (line below) then short-
    // circuits because URL == canonical state.
    router.replace('/', { scroll: false });
  }, [router]);

  // URL ↔ filter state sync. Bidirectional:
  //   - URL changes (deep-link, back/forward button) → mirror into local state
  //   - Local state changes (user toggles a filter) → router.push so the
  //     change is shareable AND back-button-reversible
  // Both halves are guarded by diff checks so they don't loop. Reads
  // happen first on each render; writes only fire when the canonical URL
  // for the current state differs from the URL we actually see.
  const filterReadInFlightRef = useRef(false);
  useEffect(() => {
    const seg = searchParams?.get('segment') || 'all';
    const status = searchParams?.get('status') || 'all';
    const minRaw = searchParams?.get('min');
    const q = searchParams?.get('q') || '';
    const sort = (searchParams?.get('sort') as SortKey | null) || DEFAULT_SORT;
    const minN = minRaw ? parseInt(minRaw, 10) : 0;
    const safeMin = Number.isFinite(minN) && minN >= 0 && minN <= 100 ? minN : 0;
    // Suppress the immediate write-back during read-driven state updates.
    filterReadInFlightRef.current = true;
    if (seg !== filterSegment) setFilterSegment(seg);
    if (status !== filterAuditStatus) setFilterAuditStatus(status);
    if (safeMin !== filterMinScore) setFilterMinScore(safeMin);
    if (q !== searchTerm) setSearchTerm(q);
    if (sort !== sortKey) setSortKey(sort);
    // Release the suppression in the next tick — by then the state-driven
    // re-render has already happened and the write effect's diff check
    // will short-circuit because URL == canonical state.
    queueMicrotask(() => { filterReadInFlightRef.current = false; });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  useEffect(() => {
    if (filterReadInFlightRef.current) return;
    const params = new URLSearchParams();
    if (filterSegment !== 'all') params.set('segment', filterSegment);
    if (filterAuditStatus !== 'all') params.set('status', filterAuditStatus);
    if (filterMinScore > 0) params.set('min', String(filterMinScore));
    if (searchTerm) params.set('q', searchTerm);
    if (sortKey !== DEFAULT_SORT) params.set('sort', sortKey);
    const qs = params.toString();
    const target = qs ? `/?${qs}` : '/';
    if (typeof window !== 'undefined' && window.location.pathname + window.location.search !== target) {
      router.push(target, { scroll: false });
    }
  }, [filterSegment, filterAuditStatus, filterMinScore, searchTerm, sortKey, router]);

  return (
    <div
      className="dashboard-container"
      data-testid="dashboard-root"
      onDragEnter={onDashboardDragEnter}
      onDragOver={onDashboardDragOver}
      onDragLeave={onDashboardDragLeave}
      onDrop={onDashboardDrop}
    >
      {isDragging && (
        <div
          data-testid="drop-overlay"
          role="status"
          aria-live="polite"
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 600,
            background: 'var(--primary-tint-15, rgba(99,102,241,0.18))',
            border: '3px dashed var(--primary, hsl(234, 89%, 64%))',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            pointerEvents: 'none',
            fontSize: '1.1rem',
            fontWeight: 600,
            color: 'var(--text-primary)',
          }}
        >
          Drop CSV to import
        </div>
      )}
      {/* Toast Notifications */}
      {toasts.length > 0 && (
        <div className="toast-container" role="status" aria-live="polite">
          {toasts.map(t => (
            <div key={t.id} className={`toast toast-${t.type}`}>{t.message}</div>
          ))}
        </div>
      )}
      <a href="#main-content" className="skip-link">{tDash('skipLink')}</a>
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
            style={{ background: 'var(--surface-muted)', border: '1px solid var(--border-subtle)', borderRadius: '10px', padding: '0.5rem', cursor: 'pointer', color: 'var(--text-primary)', display: 'flex', alignItems: 'center', justifyContent: 'center', minWidth: '44px', minHeight: '44px' }}
            aria-label={tDash('openMenu')}
            title={tDash('openMenuTitle')}
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
                 aria-label={tDash('stopProcessing')}
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
            <span style={{ fontSize: '0.8rem', fontWeight: 700, color: 'var(--primary-strong)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: '0.5rem', display: 'block' }}>{tDash('kicker')}</span>
            <h1 style={{ marginBottom: '0.5rem' }}>{tDash('heroTitle')}</h1>
            <p style={{ color: 'var(--text-muted)', fontSize: '1rem', fontWeight: 400 }}>Orchestrating AI-driven auditing for high-conversion prospecting.</p>
          </div>
          <div className="header-actions">
            <button
              className="btn-secondary"
              onClick={processAll}
              disabled={loading}
              aria-busy={loading}
            >
              <Play size={18} /> Audit All
            </button>
            <button
              className="btn-primary"
              onClick={startMassivePipeline}
              disabled={loading || !!(orchestratorJob && (orchestratorJob.status === 'running' || orchestratorJob.status === 'starting'))}
              aria-busy={loading || !!(orchestratorJob && (orchestratorJob.status === 'running' || orchestratorJob.status === 'starting'))}
              style={{ background: 'var(--primary)', border: 'none' }}
            >
              {orchestratorJob && (orchestratorJob.status === 'running' || orchestratorJob.status === 'starting') ? (
                <Loader2 size={18} className="animate-spin" aria-hidden="true" />
              ) : (
                <Zap size={18} aria-hidden="true" />
              )}
              AI Orchestrate
            </button>
            <button
              className="btn-secondary"
              onClick={handleDeepHuntAll}
              disabled={loading}
              aria-busy={loading}
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
              disabled={loading || leads.length === 0 || exportingKind !== null}
              aria-busy={exportingKind === 'full'}
            >
              {exportingKind === 'full' ? (
                <><Loader2 size={18} className="animate-spin" aria-hidden="true" /> {exportLabel('Export Full')}</>
              ) : (
                <><Download size={18} /> Export Full</>
              )}
            </button>
            <button
              className="btn-secondary"
              onClick={handleDownloadOutreachCsv}
              disabled={loading || leads.length === 0 || exportingKind !== null}
              aria-busy={exportingKind === 'outreach'}
            >
              {exportingKind === 'outreach' ? (
                <><Loader2 size={18} className="animate-spin" aria-hidden="true" /> {exportLabel('CRM Export')}</>
              ) : (
                <><FileDown size={18} /> CRM Export</>
              )}
            </button>
            <button
              className="btn-primary"
              onClick={() => document.getElementById('csv-upload')?.click()}
              disabled={loading}
              aria-busy={loading}
            >
              <Upload size={18} /> Import CSV
            </button>
          </div>
        </header>

        <HealthChart leads={leads} />

        <StatsCards leads={leads} totalLeads={totalLeads} />

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
              sortKey={sortKey}
              setSortKey={setSortKey}
              segmentOptions={segmentOptions}
              onClearFilters={clearFilters}
              hasActiveFilters={hasActiveFilters}
              showDemo={showDemo}
              setShowDemo={setShowDemo}
            />

            <LeadTable
              leads={filteredLeads}
              loading={loading}
              searchTerm={searchTerm}
              totalLeadCount={leads.length}
              processingLeads={processingLeads}
              isDrafting={isDrafting}
              activeLeadKey={activeLead?.unique_key}
              hasMore={hasMore}
              nextCursor={nextCursor}
              isLoadingMore={isLoadingMore}
              onLoadMore={loadMoreLeads}
              onEnrichLead={handleEnrichLead}
              onDeepHunt={handleDeepHunt}
              onDraftOutreach={handleDraftOutreach}
              onProcessLead={processLead}
            />
          </div>
        </div>
      </div>
    </main>

      <AIChat onExecute={handleExecutePlan} sidebarCollapsed={isSidebarCollapsed} hidden={showSettings || !!outreachDraft || showDiscoveryModal || !!campaign} />

      {/* Outreach Draft Modal */}
      {outreachDraft && (
        <div ref={outreachModalRef} role="dialog" aria-modal="true" aria-labelledby="outreach-modal-title" className="modal-backdrop" onClick={(e) => { if (e.target === e.currentTarget) setOutreachDraft(null); }}>
          <div className="card" style={{ width: '100%', maxWidth: 'min(600px, 95vw)', padding: 'clamp(1rem, 5vw, 2.5rem)', position: 'relative', border: '1px solid var(--primary)', maxHeight: '90vh', overflowY: 'auto' }}>
            <button
              onClick={() => setOutreachDraft(null)}
              aria-label={tOutreach('close')}
              style={{ position: 'absolute', right: '1.5rem', top: '1.5rem', background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', minWidth: '44px', minHeight: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            >
              <X size={24} />
            </button>
            <h2 id="outreach-modal-title" style={{ marginBottom: '0.5rem', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <Mail color="var(--primary)" aria-hidden="true" /> Outreach for {outreachDraft.leadName}
            </h2>
            {outreachDraft.leadEmail && (() => {
              const placeholderDomains = ['sentry.wixpress.com', 'sentry.io', 'wixpress.com', 'wix.com', 'squarespace.com', 'shopify.com', 'wordpress.com', 'cloudflare.com'];
              const isPlaceholder = placeholderDomains.some(d => outreachDraft.leadEmail!.toLowerCase().includes(d));
              return (
                <div style={{ marginBottom: '1.5rem' }}>
                  <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                    To:{' '}
                    <a
                      href={`mailto:${encodeURIComponent(outreachDraft.leadEmail)}${outreachDraft.subject ? `?subject=${encodeURIComponent(outreachDraft.subject)}&body=${encodeURIComponent(outreachDraft.text)}` : ''}`}
                      rel="noopener noreferrer"
                      style={{ color: isPlaceholder ? 'var(--warning)' : 'var(--primary)' }}
                      title="Open in default mail client (prefilled subject + body)"
                    >
                      {outreachDraft.leadEmail}
                    </a>
                  </div>
                  {isPlaceholder && (
                    <div role="alert" style={{ marginTop: '0.5rem', padding: '0.5rem 0.75rem', background: 'var(--warning-tint)', border: '1px solid var(--warning)', borderRadius: '6px', fontSize: '0.75rem', color: 'var(--warning-strong, var(--warning))' }}>
                      ⚠ This looks like a tracking-tool email (Wix/Sentry/CMS placeholder), not the real owner inbox. Run Harvest Contact Details or open the website directly to find a real contact.
                    </div>
                  )}
                </div>
              );
            })()}
            {!outreachDraft.leadEmail && (
              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '1.5rem', fontStyle: 'italic' }}>
                No email on file — run Harvest Contact Details first.
              </div>
            )}

            {outreachDraft.subject && (
              <div style={{ marginBottom: '1rem', display: 'flex', alignItems: 'baseline', gap: '0.5rem' }}>
                <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.05em', minWidth: '60px' }}>{tOutreach('subject')}</span>
                <span style={{ fontSize: '1rem', color: 'var(--text-primary)', fontWeight: 600 }}>{outreachDraft.subject}</span>
              </div>
            )}

            <div
              onCopy={(e) => {
                e.preventDefault();
                const sel = window.getSelection()?.toString() || outreachDraft.text;
                e.clipboardData.setData('text/plain', sel);
              }}
              style={{ background: 'var(--surface-muted)', padding: '1.5rem', borderRadius: '12px', color: 'var(--text-primary)', lineHeight: 1.6, whiteSpace: 'pre-wrap', marginBottom: '1rem', border: '1px solid var(--border-subtle)', fontSize: '0.95rem' }}
            >
              {outreachDraft.text}
            </div>

            {activeLead?.email_hook && (
              <div style={{ marginBottom: '1.5rem', padding: '1rem', background: 'rgba(165, 180, 252, 0.05)', borderRadius: '12px', border: '1px dashed rgba(165, 180, 252, 0.3)', position: 'relative' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.5rem' }}>
                  <div style={{ fontSize: '0.65rem', color: 'var(--primary-light)', textTransform: 'uppercase', fontWeight: 600 }}>{tOutreach('suggestedHook')}</div>
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(activeLead.email_hook || '');
                      setCopiedHookType('email');
                      setTimeout(() => setCopiedHookType(null), 2000);
                    }}
                    style={{ background: 'none', border: 'none', color: 'var(--primary-light)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.7rem', minHeight: '32px', padding: '0.35rem 0.5rem' }}
                  >
                    {copiedHookType === 'email' ? <><Check size={12} /> {tCommon('copied')}</> : <><Copy size={12} /> {tOutreach('copyHook')}</>}
                  </button>
                </div>
                <p style={{ fontSize: '0.9rem', fontStyle: 'italic', margin: 0, color: 'var(--primary-light)' }}>&quot;{activeLead.email_hook}&quot;</p>
              </div>
            )}

            {linkedinDraft && (
              <div style={{ marginTop: '0', padding: '1.5rem', background: 'var(--linkedin-tint)', borderRadius: '12px', border: '1px solid rgba(10, 102, 194, 0.2)', marginBottom: '2rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem', color: 'var(--linkedin)' }}>
                  <Linkedin size={18} aria-hidden="true" />
                  <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 700 }}>{tOutreach('linkedinTitle')}</h3>
                </div>
                <p style={{ fontSize: '0.9rem', lineHeight: '1.6', color: 'var(--text-primary)', whiteSpace: 'pre-wrap', margin: 0 }}>
                  {linkedinDraft}
                </p>
                {activeLead?.linkedin_hook && (
                  <div style={{ marginTop: '1rem', padding: '0.75rem', background: 'var(--surface-muted)', borderRadius: '8px', borderLeft: '3px solid var(--linkedin)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.25rem' }}>
                      <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>{tOutreach('connectionHook')}</div>
                      <button
                        onClick={() => {
                          navigator.clipboard.writeText(activeLead.linkedin_hook || '');
                          setCopiedHookType('linkedin');
                          setTimeout(() => setCopiedHookType(null), 2000);
                        }}
                        style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.65rem', minHeight: '32px', padding: '0.35rem 0.5rem' }}
                      >
                        {copiedHookType === 'linkedin' ? <><Check size={10} /> {tCommon('copied')}</> : <><Copy size={10} /> {tCommon('copy')}</>}
                      </button>
                    </div>
                    <p style={{ fontSize: '0.8rem', margin: 0, color: 'var(--text-primary)' }}>{activeLead.linkedin_hook}</p>
                  </div>
                )}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '1rem', gap: '0.5rem', flexWrap: 'wrap' }}>
                   <p style={{ fontSize: '0.7rem', color: 'var(--text-muted)', margin: 0 }}>
                    {linkedinDraft.length}/300 characters
                  </p>
                  <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                    {activeLead?.linkedin ? (
                      <a
                        href={ensureProtocol(activeLead.linkedin)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="btn-secondary"
                        style={{ padding: '0.4rem 1rem', fontSize: '0.75rem', background: 'rgba(10, 102, 194, 0.2)', borderColor: 'var(--linkedin)', color: 'var(--text-white)', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '0.35rem' }}
                        title="Open LinkedIn profile in new tab — click Connect there, then paste"
                      >
                        <Linkedin size={12} /> Open Profile
                      </a>
                    ) : (
                      <a
                        href={`https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(outreachDraft.leadName)}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="btn-secondary"
                        style={{ padding: '0.4rem 1rem', fontSize: '0.75rem', background: 'rgba(10, 102, 194, 0.2)', borderColor: 'var(--linkedin)', color: 'var(--text-white)', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '0.35rem' }}
                        title="No LinkedIn URL on file — search LinkedIn for this company"
                      >
                        <Linkedin size={12} /> Search LinkedIn
                      </a>
                    )}
                    <button
                      className="btn-secondary"
                      style={{ padding: '0.4rem 1rem', fontSize: '0.75rem', background: 'rgba(10, 102, 194, 0.2)', borderColor: 'var(--linkedin)', color: 'var(--text-white)' }}
                      onClick={async () => {
                        try { await navigator.clipboard.writeText(linkedinDraft); } catch { showToast('Copy failed — clipboard blocked.', 'error'); return; }
                        setCopiedAction('invite');
                        setTimeout(() => setCopiedAction(p => p === 'invite' ? null : p), 2000);
                        showToast("LinkedIn message copied — paste into the Connect dialog.", 'success');
                      }}
                      title="Copy this message text — LinkedIn has no API for sending invites with prefilled text, so paste manually after clicking Connect on the profile."
                    >
                      {copiedAction === 'invite' ? (
                        <><Check size={12} style={{ marginRight: '0.3rem', verticalAlign: 'middle' }} /> {tCommon('copied')}</>
                      ) : (
                        <><Copy size={12} style={{ marginRight: '0.3rem', verticalAlign: 'middle' }} /> {tOutreach('copyMessage')}</>
                      )}
                    </button>
                  </div>
                </div>
              </div>
            )}
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
              <button
                className="btn-primary"
                style={{ flex: '1 1 160px' }}
                onClick={async () => {
                  try { await navigator.clipboard.writeText(outreachDraft.text); } catch { showToast('Copy failed — clipboard blocked.', 'error'); return; }
                  setCopiedAction('body');
                  setTimeout(() => setCopiedAction(p => p === 'body' ? null : p), 2000);
                  showToast('Draft copied to clipboard!', 'success');
                }}
              >
                {copiedAction === 'body' ? (
                  <><Check size={14} style={{ marginRight: '0.4rem', verticalAlign: 'middle' }} /> {tCommon('copied')}</>
                ) : (
                  <><Copy size={14} style={{ marginRight: '0.4rem', verticalAlign: 'middle' }} /> {tOutreach('copyBody')}</>
                )}
              </button>
              {outreachDraft.subject && (
                <button
                  className="btn-secondary"
                  style={{ flex: '1 1 140px' }}
                  onClick={async () => {
                    try { await navigator.clipboard.writeText(outreachDraft.subject!); } catch { showToast('Copy failed — clipboard blocked.', 'error'); return; }
                    setCopiedAction('subject');
                    setTimeout(() => setCopiedAction(p => p === 'subject' ? null : p), 2000);
                    showToast('Subject copied!', 'success');
                  }}
                >
                  {copiedAction === 'subject' ? (
                    <><Check size={14} style={{ marginRight: '0.4rem', verticalAlign: 'middle' }} /> {tCommon('copied')}</>
                  ) : (
                    <><Copy size={14} style={{ marginRight: '0.4rem', verticalAlign: 'middle' }} /> {tOutreach('copySubject')}</>
                  )}
                </button>
              )}
              <a
                href={`https://mail.google.com/mail/?view=cm&fs=1${outreachDraft.leadEmail ? `&to=${encodeURIComponent(outreachDraft.leadEmail)}` : ''}${outreachDraft.subject ? `&su=${encodeURIComponent(outreachDraft.subject)}` : ''}&body=${encodeURIComponent(outreachDraft.text)}`}
                target="_blank"
                rel="noopener noreferrer"
                className="btn-secondary"
                style={{ flex: '1 1 140px', textAlign: 'center', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: '0.4rem' }}
              >
                <Mail size={14} /> Open in Gmail
              </a>
              <button className="btn-secondary" style={{ flex: '1 1 100px' }} onClick={() => setOutreachDraft(null)}>
                Discard
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Discovery Modal */}
      {showDiscoveryModal && (
        <div ref={discoveryModalRef} role="dialog" aria-modal="true" aria-labelledby="discovery-modal-title" className="modal-backdrop" onClick={(e) => { if (e.target === e.currentTarget && !isDiscovering) setShowDiscoveryModal(false); }}>
          <div className="card" style={{ width: '100%', maxWidth: 'min(500px, 95vw)', padding: 'clamp(1.25rem, 4vw, 2rem)', position: 'relative', border: '1px solid var(--primary)', maxHeight: '90vh', overflowY: 'auto' }}>
            <button
              onClick={() => setShowDiscoveryModal(false)}
              aria-label={tDiscovery('close')}
              title={tCommon('closeEsc')}
              style={{ position: 'absolute', right: '1.5rem', top: '1.5rem', background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', minWidth: '44px', minHeight: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            >
              <X size={24} aria-hidden="true" />
            </button>
            <h2 id="discovery-modal-title" style={{ marginBottom: '1.5rem', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <Globe color="var(--primary)" aria-hidden="true" /> Lead Discovery Engine
            </h2>
            
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', marginBottom: '2rem' }}>
              <div>
                <label htmlFor="discovery-query" style={{ display: 'block', fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>What are you looking for? <span aria-hidden="true" style={{ color: 'var(--error)' }}>*</span></label>
                <input
                  type="text"
                  id="discovery-query"
                  required
                  value={discoveryQuery}
                  onChange={(e) => setDiscoveryQuery(e.target.value)}
                  placeholder="e.g. Dental Clinics"
                  style={{ width: '100%', background: 'var(--surface-muted)', border: '1px solid var(--glass-border)', borderRadius: '12px', padding: '0.75rem 1rem', color: 'var(--text-white)' }}
                />
              </div>
              <div>
                <label htmlFor="discovery-location" style={{ display: 'block', fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>{tDiscovery('location')} <span aria-hidden="true" style={{ color: 'var(--error)' }}>*</span></label>
                <input
                  type="text"
                  id="discovery-location"
                  required
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
                disabled={isDiscovering || !discoveryQuery.trim() || !discoveryLocation.trim()}
                aria-busy={isDiscovering}
              >
                {isDiscovering ? (
                  <>
                    <Loader2 className="animate-spin" size={18} aria-hidden="true" />
                    <span>Mining {discoveryQuery}...</span>
                  </>
                ) : (
                  <>
                    <Play size={18} aria-hidden="true" />
                    <span>{tDiscovery('startSearch')}</span>
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
        <div ref={settingsModalRef} role="dialog" aria-modal="true" aria-labelledby="settings-modal-title" className="modal-backdrop" onClick={(e) => { if (e.target === e.currentTarget) setShowSettings(false); }}>
          <div className="card" style={{ width: '100%', maxWidth: '500px', padding: 'clamp(1.25rem, 4vw, 2.5rem)', position: 'relative', border: '1px solid var(--primary)' }}>
            <button
              onClick={() => setShowSettings(false)}
              aria-label={tSettings('close')}
              style={{ position: 'absolute', right: '1.5rem', top: '1.5rem', background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', minWidth: '44px', minHeight: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            >
              <X size={24} />
            </button>
            <h2 id="settings-modal-title" style={{ marginBottom: '1.5rem', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <Settings color="var(--primary)" /> System Settings
            </h2>
            
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', marginBottom: '2rem' }}>
              <div style={{ padding: '1rem', background: 'var(--surface-elevated)', borderRadius: '12px', border: '1px solid var(--border-subtle)' }}>
                <h3 style={{ fontSize: '0.9rem', marginBottom: '0.5rem' }}>{tSettings('apiConfig')}</h3>
                <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Backend: <code>{API_BASE_URL}</code></p>
                <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Database: Supabase</p>
              </div>

              <div style={{ padding: '1rem', background: 'var(--surface-elevated)', borderRadius: '12px', border: '1px solid var(--border-subtle)' }}>
                <h3 style={{ fontSize: '0.9rem', marginBottom: '1rem' }}>{tSettings('dataExport')}</h3>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: '0.75rem' }}>
                  <button
                    className="btn-secondary"
                    style={{ fontSize: '0.8rem', justifyContent: 'center', display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}
                    disabled={isGeneratingCsv}
                    aria-busy={isGeneratingCsv}
                    onClick={async () => {
                      if (isGeneratingCsv) return;
                      setIsGeneratingCsv(true);
                      try {
                        const res = await apiFetch(`${API_BASE_URL}/export`);
                        const data = await res.json().catch(() => ({}));
                        if (!res.ok) {
                          showToast(data.detail || data.error || `Export failed (HTTP ${res.status})`, 'error');
                          return;
                        }
                        showToast(data.message || "Export generated!", 'success');
                      } catch {
                        showToast("Export generation failed.", 'error');
                      } finally {
                        setIsGeneratingCsv(false);
                      }
                    }}
                  >
                    {isGeneratingCsv ? (
                      <><Loader2 size={14} className="animate-spin" aria-hidden="true" /> Generating…</>
                    ) : 'Generate CSVs'}
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

              <div style={{ padding: '1rem', background: 'var(--surface-elevated)', borderRadius: '12px', border: '1px solid var(--border-subtle)' }}>
                <h3 style={{ fontSize: '0.9rem', marginBottom: '0.5rem' }}>{tSettings('myDataGdpr')}</h3>
                <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
                  Download a ZIP of every row tied to your account — leads, campaigns,
                  messages, and the orchestration audit log. Rate-limited to once per day.
                </p>
                <button
                  className="btn-secondary"
                  style={{ fontSize: '0.8rem', width: '100%', justifyContent: 'center', display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}
                  disabled={isExportingMyData}
                  aria-busy={isExportingMyData}
                  onClick={async () => {
                    if (isExportingMyData) return;
                    setIsExportingMyData(true);
                    try {
                      const res = await apiFetch(`${API_BASE_URL}/operator/data-export`);
                      if (!res.ok) {
                        const data = await res.json().catch(() => ({}));
                        const msg = res.status === 429
                          ? "Already exported today — try again in 24h."
                          : (data.detail || data.error || `Data export failed (HTTP ${res.status})`);
                        showToast(msg, 'error');
                        return;
                      }
                      const blob = await res.blob();
                      const url = window.URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      const ts = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '');
                      a.download = `leadscraper-export-${ts}.zip`;
                      document.body.appendChild(a);
                      a.click();
                      document.body.removeChild(a);
                      window.URL.revokeObjectURL(url);
                      showToast('Data export downloaded.', 'success');
                    } catch {
                      showToast('Data export failed — backend unreachable.', 'error');
                    } finally {
                      setIsExportingMyData(false);
                    }
                  }}
                >
                  {isExportingMyData ? (
                    <><Loader2 size={14} className="animate-spin" aria-hidden="true" /> Preparing…</>
                  ) : 'Download my data'}
                </button>
              </div>

              <div style={{ padding: '1rem', background: 'rgba(239, 68, 68, 0.05)', borderRadius: '12px', border: '1px solid var(--error-tint)' }}>
                <h3 style={{ fontSize: '0.9rem', color: 'var(--error-strong)', marginBottom: '0.5rem' }}>Danger Zone</h3>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                  {!demoRemoveOpen ? (
                    <button
                      type="button"
                      className="btn-secondary"
                      style={{ width: '100%', borderColor: 'var(--error)', color: 'var(--error)', fontSize: '0.8rem' }}
                      onClick={() => { setDemoRemoveOpen(true); setDemoRemoveText(''); }}
                    >
                      Remove all demo data
                    </button>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                      <label htmlFor="confirm-remove-demo" style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        Type <code>REMOVE DEMO</code> to confirm. This wipes every <code>is_demo=true</code> lead and any campaign messages that reference them.
                      </label>
                      <input
                        id="confirm-remove-demo"
                        type="text"
                        value={demoRemoveText}
                        onChange={(e) => setDemoRemoveText(e.target.value)}
                        placeholder="REMOVE DEMO"
                        autoComplete="off"
                        spellCheck={false}
                        style={{ background: 'var(--surface-muted)', border: '1px solid var(--border)', borderRadius: '8px', padding: '0.5rem 0.75rem', color: 'var(--text-white)', fontSize: '0.85rem', outline: 'none' }}
                      />
                      <div style={{ display: 'flex', gap: '0.5rem' }}>
                        <button
                          type="button"
                          className="btn-secondary"
                          style={{ flex: 1, fontSize: '0.8rem' }}
                          onClick={() => { setDemoRemoveOpen(false); setDemoRemoveText(''); }}
                          disabled={isRemovingDemo}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="btn-secondary"
                          style={{ flex: 1, borderColor: 'var(--error)', color: 'var(--error)', fontSize: '0.8rem' }}
                          onClick={handleRemoveDemo}
                          disabled={demoRemoveText !== 'REMOVE DEMO' || isRemovingDemo}
                          aria-busy={isRemovingDemo}
                        >
                          {isRemovingDemo ? (
                            <><Loader2 size={14} className="animate-spin" aria-hidden="true" /> Removing…</>
                          ) : 'Confirm remove'}
                        </button>
                      </div>
                    </div>
                  )}
                  <button
                    type="button"
                    className="btn-secondary"
                    style={{ width: '100%', borderColor: 'var(--error)', color: 'var(--error)', fontSize: '0.8rem' }}
                    onClick={handleClearLeads}
                  >
                    Clear All Leads
                  </button>
                </div>
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
        <div ref={campaignModalRef} role="dialog" aria-modal="true" aria-labelledby="campaign-modal-title" className="modal-backdrop" onClick={(e) => { if (e.target === e.currentTarget) setCampaign(null); }}>
          <div className="card" style={{ width: '100%', maxWidth: 'min(900px, 95vw)', maxHeight: '90vh', overflow: 'hidden', display: 'flex', flexDirection: 'column', border: '1px solid var(--primary)', borderRadius: '24px' }}>
             <div style={{ padding: '1.5rem 2rem', borderBottom: '1px solid var(--border-muted)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'var(--surface-subtle)' }}>
               <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                 <div style={{ background: 'var(--primary)', borderRadius: '10px', padding: '0.6rem' }}>
                    <Zap size={20} color="white" />
                 </div>
                 <div>
                    <h2 id="campaign-modal-title" style={{ fontSize: '1.25rem', fontWeight: 700, margin: 0 }}>{tCampaign('title')}</h2>
                    <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', margin: 0 }}>Personalized drafts for {campaign.length} high-priority leads.</p>
                 </div>
               </div>
               <button
                 onClick={() => setCampaign(null)}
                 aria-label={tCampaign('close')}
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
                              <h3 style={{ margin: 0, fontSize: '1.1rem', color: 'var(--text-white)' }}>{item.company}</h3>
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
                <button className="btn-secondary" onClick={() => setCampaign(null)}>{tCampaign('closeLibrary')}</button>
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
