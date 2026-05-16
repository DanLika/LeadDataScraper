'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useFocusTrap } from '@/utils/useFocusTrap';
import {
  Mail, Play, Pause, Download, Plus, ArrowLeft,
  Loader2, Send, Users, CheckCircle,
  Eye, X, Shield, Menu
} from 'lucide-react';
import Link from 'next/link';
import { API_BASE_URL, apiFetch } from '@/utils/apiConfig';
import { useEscape } from '@/utils/useEscape';
import Sidebar from '../components/Sidebar';
import AIChat from '../components/AIChat';
import { Linkedin } from '../components/BrandIcons';

interface Campaign {
  id: string;
  name: string;
  status: string;
  channel: string;
  segment_filter?: string;
  total_leads: number;
  sent_count: number;
  reply_count: number;
  created_at: string;
}

interface CampaignMessage {
  id: string;
  lead_unique_key: string;
  channel: string;
  subject?: string;
  body: string;
  status: string;
}

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [selectedCampaign, setSelectedCampaign] = useState<Campaign | null>(null);
  const [messages, setMessages] = useState<CampaignMessage[]>([]);
  const [totalMessages, setTotalMessages] = useState<number>(0);
  const [messageStats, setMessageStats] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [previewMessage, setPreviewMessage] = useState<CampaignMessage | null>(null);
  const previewModalRef = useRef<HTMLDivElement>(null);
  useFocusTrap(previewModalRef, !!previewMessage);

  // Form state
  const [newName, setNewName] = useState('');
  const [newChannel, setNewChannel] = useState('email');
  const [newSegment, setNewSegment] = useState('');
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  useEscape(() => {
    setIsSidebarOpen(false);
    requestAnimationFrame(() => {
      (document.querySelector('button[aria-label="Open menu"]') as HTMLElement | null)?.focus();
    });
  }, isSidebarOpen);

  const fetchCampaigns = useCallback(async () => {
    try {
      const resp = await apiFetch(`${API_BASE_URL}/campaigns`);
      const data = await resp.json();
      setCampaigns(data.campaigns || []);
    } catch (err) {
      console.error('Failed to fetch campaigns:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchCampaignDetails = useCallback(async (id: string) => {
    try {
      const resp = await apiFetch(`${API_BASE_URL}/campaigns/${id}`);
      const data = await resp.json();
      if (data.campaign) {
        setSelectedCampaign(data.campaign);
        setMessages(data.messages || []);
        setTotalMessages(data.total_messages || (data.messages || []).length);
        setMessageStats(data.stats || {});
      }
    } catch (err) {
      console.error('Failed to fetch campaign details:', err);
    }
  }, []);

  useEffect(() => {
    fetchCampaigns();
  }, [fetchCampaigns]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const resp = await apiFetch(`${API_BASE_URL}/campaigns`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName, channel: newChannel, segment_filter: newSegment || null }),
      });
      const data = await resp.json();
      if (data.campaign) {
        setCampaigns(prev => [data.campaign, ...prev]);
        setShowCreate(false);
        setNewName('');
        setNewSegment('');
      }
    } catch (err) {
      console.error('Failed to create campaign:', err);
    } finally {
      setCreating(false);
    }
  };

  const handleGenerate = async (campaignId: string) => {
    setGenerating(true);
    try {
      await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}/generate`, { method: 'POST' });
      await fetchCampaignDetails(campaignId);
    } catch (err) {
      console.error('Failed to generate messages:', err);
    } finally {
      setGenerating(false);
    }
  };

  const handleStartPause = async (campaignId: string, action: 'start' | 'pause') => {
    try {
      await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}/${action}`, { method: 'POST' });
      await fetchCampaigns();
      if (selectedCampaign?.id === campaignId) {
        await fetchCampaignDetails(campaignId);
      }
    } catch (err) {
      console.error('Failed to %s campaign:', action, err);
    }
  };

  const handleExport = async (campaignId: string) => {
    window.open(`${API_BASE_URL}/campaigns/${campaignId}/export`, '_blank');
  };

  const statusColor = (status: string) => {
    switch (status) {
      case 'active': return 'var(--success)';
      case 'paused': return 'var(--warning)';
      case 'completed': return 'var(--primary)';
      case 'draft': return 'var(--text-muted)';
      default: return 'var(--text-muted)';
    }
  };

  const channelIcon = (channel: string) => {
    switch (channel) {
      case 'email': return <Mail size={16} />;
      case 'linkedin': return <Linkedin size={16} />;
      default: return <Send size={16} />;
    }
  };

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
        leads={[]}
        fetchingInsights={false}
        insights={null}
        fetchInsights={() => {}}
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

        <div className="main-content-wrapper" style={{ padding: '2rem' }}>
        <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '1rem', marginBottom: '2rem' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '0.5rem' }}>
              <Link
                href="/"
                aria-label="Back to dashboard"
                style={{ color: 'var(--text-muted)', textDecoration: 'none', display: 'flex', alignItems: 'center', gap: '0.5rem' }}
              >
                <ArrowLeft size={20} aria-hidden="true" />
                <Shield size={20} aria-hidden="true" />
              </Link>
              <h1 style={{ margin: 0, fontSize: '1.75rem', fontWeight: 800 }}>Outreach Campaigns</h1>
            </div>
            <p style={{ color: 'var(--text-muted)', fontSize: '1rem', margin: 0 }}>Manage email and LinkedIn outreach campaigns for your leads.</p>
          </div>
          <button className="btn-primary" onClick={() => setShowCreate(true)} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Plus size={18} /> New Campaign
          </button>
        </div>

        {showCreate && (
          <div className="card" style={{ marginBottom: '2rem', border: '1px solid hsla(var(--primary-hsl), 0.3)' }}>
            <h2 style={{ marginBottom: '1.5rem' }}>Create New Campaign</h2>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div>
                <label htmlFor="campaign-name" style={{ display: 'block', fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>Campaign Name</label>
                <input
                  id="campaign-name"
                  value={newName}
                  onChange={e => setNewName(e.target.value)}
                  placeholder="e.g. Q1 Cold Outreach - Dental Clinics"
                  style={{ width: '100%', padding: '0.75rem', background: 'var(--surface-muted)', border: '1px solid var(--border-muted)', borderRadius: '8px', color: 'var(--text-white)', fontSize: '0.95rem' }}
                />
              </div>
              <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
                <div style={{ flex: 1, minWidth: '200px' }}>
                  <label htmlFor="campaign-channel" style={{ display: 'block', fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>Channel</label>
                  <select
                    id="campaign-channel"
                    value={newChannel}
                    onChange={e => setNewChannel(e.target.value)}
                    style={{ width: '100%', padding: '0.75rem', background: 'var(--surface-muted)', border: '1px solid var(--border-muted)', borderRadius: '8px', color: 'var(--text-white)', fontSize: '0.95rem' }}
                  >
                    <option value="email">Email</option>
                    <option value="linkedin">LinkedIn</option>
                    <option value="multi">Multi-channel (Email + LinkedIn)</option>
                  </select>
                </div>
                <div style={{ flex: 1, minWidth: '200px' }}>
                  <label htmlFor="campaign-segment" style={{ display: 'block', fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>Segment Filter (optional)</label>
                  <input
                    id="campaign-segment"
                    value={newSegment}
                    onChange={e => setNewSegment(e.target.value)}
                    placeholder="e.g. Performance Optimization"
                    style={{ width: '100%', padding: '0.75rem', background: 'var(--surface-muted)', border: '1px solid var(--border-muted)', borderRadius: '8px', color: 'var(--text-white)', fontSize: '0.95rem' }}
                  />
                </div>
              </div>
              <div style={{ display: 'flex', gap: '0.75rem' }}>
                <button className="btn-primary" onClick={handleCreate} disabled={creating || !newName.trim()} aria-busy={creating} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  {creating ? <Loader2 size={16} className="animate-spin" aria-hidden="true" /> : <Plus size={16} aria-hidden="true" />}
                  Create Campaign
                </button>
                <button className="btn-secondary" onClick={() => setShowCreate(false)}>Cancel</button>
              </div>
            </div>
          </div>
        )}

        {/* Campaign Detail View */}
        {selectedCampaign && (
          <div className="card" style={{ marginBottom: '2rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1.5rem', flexWrap: 'wrap', gap: '1rem' }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                  <button aria-label="Back to campaign list" onClick={() => { setSelectedCampaign(null); setMessages([]); }} style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}>
                    <ArrowLeft size={18} />
                  </button>
                  <h2 style={{ margin: 0 }}>{selectedCampaign.name}</h2>
                  <span style={{ padding: '0.25rem 0.75rem', borderRadius: '20px', fontSize: '0.75rem', fontWeight: 600, background: `${statusColor(selectedCampaign.status)}22`, color: statusColor(selectedCampaign.status) }}>
                    {selectedCampaign.status}
                  </span>
                </div>
                <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '0.5rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  {channelIcon(selectedCampaign.channel)} {selectedCampaign.channel} campaign
                  {selectedCampaign.segment_filter && ` | Segment: ${selectedCampaign.segment_filter}`}
                </p>
              </div>
              <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                <button className="btn-primary" onClick={() => handleGenerate(selectedCampaign.id)} disabled={generating} aria-busy={generating} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  {generating ? <Loader2 size={16} className="animate-spin" aria-hidden="true" /> : <Send size={16} aria-hidden="true" />}
                  Generate Messages
                </button>
                {selectedCampaign.status === 'active' ? (
                  <button className="btn-secondary" onClick={() => handleStartPause(selectedCampaign.id, 'pause')} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <Pause size={16} /> Pause
                  </button>
                ) : (
                  <button className="btn-secondary" onClick={() => handleStartPause(selectedCampaign.id, 'start')} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <Play size={16} /> Start
                  </button>
                )}
                <button className="btn-secondary" onClick={() => handleExport(selectedCampaign.id)} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <Download size={16} /> Export CSV
                </button>
              </div>
            </div>

            {/* Stats */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '1rem', marginBottom: '1.5rem' }}>
              {[
                { label: 'Total Leads', value: selectedCampaign.total_leads, icon: <Users size={20} />, color: 'var(--primary)' },
                { label: 'Pending', value: messageStats.pending || 0, icon: <Send size={20} />, color: 'var(--text-muted)' },
                { label: 'Sent', value: messageStats.sent || 0, icon: <CheckCircle size={20} />, color: 'var(--success)' },
                { label: 'Replied', value: messageStats.replied || 0, icon: <Mail size={20} />, color: 'var(--warning)' },
              ].map((stat, i) => (
                <div key={i} className="card" style={{ textAlign: 'center', padding: '1.25rem' }}>
                  <div style={{ color: stat.color, marginBottom: '0.5rem' }}>{stat.icon}</div>
                  <div style={{ fontSize: '1.5rem', fontWeight: 800 }}>{stat.value}</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{stat.label}</div>
                </div>
              ))}
            </div>

            {/* Messages List */}
            {messages.length > 0 && (
              <div>
                <h3 style={{ marginBottom: '1rem', fontSize: '1rem' }}>Messages ({totalMessages})</h3>
                <div style={{ maxHeight: 'min(400px, 50vh)', overflowY: 'auto' }}>
                  {messages.slice(0, 50).map((msg, idx) => (
                    <div key={idx} style={{
                      padding: '1rem',
                      borderBottom: '1px solid var(--surface-muted)',
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      gap: '1rem'
                    }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem' }}>
                          {channelIcon(msg.channel)}
                          <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>{msg.lead_unique_key}</span>
                          <span style={{
                            padding: '0.15rem 0.5rem',
                            borderRadius: '10px',
                            fontSize: '0.7rem',
                            background: msg.status === 'sent' ? 'var(--success-tint)' : 'var(--surface-muted)',
                            color: msg.status === 'sent' ? 'var(--success)' : 'var(--text-muted)'
                          }}>
                            {msg.status}
                          </span>
                        </div>
                        {msg.subject && <div style={{ fontSize: '0.8rem', color: 'var(--text-primary)' }}>{msg.subject}</div>}
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {msg.body}
                        </div>
                      </div>
                      <button
                        aria-label="Preview message"
                        onClick={() => setPreviewMessage(msg)}
                        style={{ background: 'var(--surface-muted)', border: 'none', borderRadius: '8px', padding: '0.5rem', color: 'var(--text-muted)', cursor: 'pointer', minWidth: '44px', minHeight: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                      >
                        <Eye size={16} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Message Preview Modal */}
        {previewMessage && (
          <div
            ref={previewModalRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="preview-modal-title"
            tabIndex={-1}
            className="modal-backdrop"
            onClick={(e) => { if (e.target === e.currentTarget) setPreviewMessage(null); }}
            onKeyDown={(e) => { if (e.key === 'Escape') setPreviewMessage(null); }}
          >
            <div className="card" style={{ maxWidth: '600px', width: '90%', maxHeight: '80vh', overflowY: 'auto' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                <h2 id="preview-modal-title" style={{ margin: 0 }}>Message Preview</h2>
                <button onClick={() => setPreviewMessage(null)} aria-label="Close preview" style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', minWidth: '44px', minHeight: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <X size={20} />
                </button>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                <div>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>Channel</span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    {channelIcon(previewMessage.channel)} {previewMessage.channel}
                  </div>
                </div>
                <div>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>Lead</span>
                  <div>{previewMessage.lead_unique_key}</div>
                </div>
                {previewMessage.subject && (
                  <div>
                    <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>Subject</span>
                    <div style={{ fontWeight: 600 }}>{previewMessage.subject}</div>
                  </div>
                )}
                <div>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>Body</span>
                  <div style={{
                    background: 'var(--surface-elevated)',
                    padding: '1rem',
                    borderRadius: '8px',
                    whiteSpace: 'pre-wrap',
                    lineHeight: 1.6
                  }}>
                    {previewMessage.body}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Campaign List */}
        {!selectedCampaign && (
          <>
            {loading ? (
              <div style={{ textAlign: 'center', padding: '4rem' }}>
                <Loader2 size={32} className="animate-spin" style={{ color: 'var(--primary)' }} />
                <p style={{ color: 'var(--text-muted)', marginTop: '1rem' }}>Loading campaigns...</p>
              </div>
            ) : campaigns.length === 0 ? (
              <div className="card" style={{ textAlign: 'center', padding: '4rem' }}>
                <Mail size={48} style={{ color: 'var(--text-dim)', marginBottom: '1rem' }} />
                <h2 style={{ color: 'var(--text-primary)', marginBottom: '0.5rem' }}>No Campaigns Yet</h2>
                <p style={{ color: 'var(--text-muted)', marginBottom: '1.5rem' }}>Create your first outreach campaign to start reaching leads.</p>
                <button className="btn-primary" onClick={() => setShowCreate(true)} style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem' }}>
                  <Plus size={18} /> Create Campaign
                </button>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                {campaigns.map(camp => (
                  <div
                    key={camp.id}
                    className="card"
                    style={{ cursor: 'pointer' }}
                    role="button"
                    tabIndex={0}
                    onClick={() => { setSelectedCampaign(camp); fetchCampaignDetails(camp.id); }}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelectedCampaign(camp); fetchCampaignDetails(camp.id); } }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
                      <div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.25rem' }}>
                          {channelIcon(camp.channel)}
                          <span style={{ fontWeight: 700, fontSize: '1.1rem' }}>{camp.name}</span>
                          <span style={{
                            padding: '0.2rem 0.6rem', borderRadius: '20px', fontSize: '0.7rem', fontWeight: 600,
                            background: `${statusColor(camp.status)}22`, color: statusColor(camp.status)
                          }}>
                            {camp.status}
                          </span>
                        </div>
                        {camp.segment_filter && (
                          <span style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>Segment: {camp.segment_filter}</span>
                        )}
                      </div>
                      <div style={{ display: 'flex', gap: '2rem', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                        <div><strong style={{ color: 'var(--text-primary)' }}>{camp.total_leads}</strong> leads</div>
                        <div><strong style={{ color: 'var(--success)' }}>{camp.sent_count}</strong> sent</div>
                        <div><strong style={{ color: 'var(--warning)' }}>{camp.reply_count}</strong> replies</div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
        </div>
      </main>

      <AIChat sidebarCollapsed={isSidebarCollapsed} />
    </div>
  );
}
