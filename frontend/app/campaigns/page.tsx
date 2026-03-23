'use client';

import { useState, useEffect, useCallback } from 'react';
import {
  Mail, Linkedin, Play, Pause, Download, Plus, ArrowLeft,
  Loader2, Send, Users, CheckCircle, Menu,
  Eye, X, Shield
} from 'lucide-react';
import Link from 'next/link';
import { API_BASE_URL } from '@/utils/apiConfig';

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

  // Form state
  const [newName, setNewName] = useState('');
  const [newChannel, setNewChannel] = useState('email');
  const [newSegment, setNewSegment] = useState('');

  const fetchCampaigns = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE_URL}/campaigns`);
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
      const resp = await fetch(`${API_BASE_URL}/campaigns/${id}`);
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
      const resp = await fetch(`${API_BASE_URL}/campaigns`, {
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
      await fetch(`${API_BASE_URL}/campaigns/${campaignId}/generate`, { method: 'POST' });
      await fetchCampaignDetails(campaignId);
    } catch (err) {
      console.error('Failed to generate messages:', err);
    } finally {
      setGenerating(false);
    }
  };

  const handleStartPause = async (campaignId: string, action: 'start' | 'pause') => {
    try {
      await fetch(`${API_BASE_URL}/campaigns/${campaignId}/${action}`, { method: 'POST' });
      await fetchCampaigns();
      if (selectedCampaign?.id === campaignId) {
        await fetchCampaignDetails(campaignId);
      }
    } catch (err) {
      console.error(`Failed to ${action} campaign:`, err);
    }
  };

  const handleExport = async (campaignId: string) => {
    window.open(`${API_BASE_URL}/campaigns/${campaignId}/export`, '_blank');
  };

  const statusColor = (status: string) => {
    switch (status) {
      case 'active': return '#10b981';
      case 'paused': return '#f59e0b';
      case 'completed': return '#6366f1';
      case 'draft': return '#94a3b8';
      default: return '#94a3b8';
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
      <main className="main-content" style={{ marginLeft: 0, width: '100%' }}>
        <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '1rem', marginBottom: '2rem' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '0.5rem' }}>
              <Link href="/" style={{ color: '#94a3b8', textDecoration: 'none', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <ArrowLeft size={20} />
                <Shield size={20} />
              </Link>
              <h1 style={{ margin: 0, fontSize: '1.75rem', fontWeight: 800 }}>Outreach Campaigns</h1>
            </div>
            <p style={{ color: '#94a3b8', fontSize: '1rem', margin: 0 }}>Manage email and LinkedIn outreach campaigns for your leads.</p>
          </div>
          <button className="btn-primary" onClick={() => setShowCreate(true)} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <Plus size={18} /> New Campaign
          </button>
        </div>

        {/* Create Campaign Modal */}
        {showCreate && (
          <div className="card" style={{ marginBottom: '2rem', border: '1px solid rgba(99, 102, 241, 0.3)' }}>
            <h3 style={{ marginBottom: '1.5rem' }}>Create New Campaign</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div>
                <label style={{ display: 'block', fontSize: '0.85rem', color: '#94a3b8', marginBottom: '0.5rem' }}>Campaign Name</label>
                <input
                  value={newName}
                  onChange={e => setNewName(e.target.value)}
                  placeholder="e.g. Q1 Cold Outreach - Dental Clinics"
                  style={{ width: '100%', padding: '0.75rem', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', color: 'white', fontSize: '0.95rem' }}
                />
              </div>
              <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
                <div style={{ flex: 1, minWidth: '200px' }}>
                  <label style={{ display: 'block', fontSize: '0.85rem', color: '#94a3b8', marginBottom: '0.5rem' }}>Channel</label>
                  <select
                    value={newChannel}
                    onChange={e => setNewChannel(e.target.value)}
                    style={{ width: '100%', padding: '0.75rem', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', color: 'white', fontSize: '0.95rem' }}
                  >
                    <option value="email">Email</option>
                    <option value="linkedin">LinkedIn</option>
                    <option value="multi">Multi-channel (Email + LinkedIn)</option>
                  </select>
                </div>
                <div style={{ flex: 1, minWidth: '200px' }}>
                  <label style={{ display: 'block', fontSize: '0.85rem', color: '#94a3b8', marginBottom: '0.5rem' }}>Segment Filter (optional)</label>
                  <input
                    value={newSegment}
                    onChange={e => setNewSegment(e.target.value)}
                    placeholder="e.g. Performance Optimization"
                    style={{ width: '100%', padding: '0.75rem', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', color: 'white', fontSize: '0.95rem' }}
                  />
                </div>
              </div>
              <div style={{ display: 'flex', gap: '0.75rem' }}>
                <button className="btn-primary" onClick={handleCreate} disabled={creating || !newName.trim()} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  {creating ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
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
                  <button onClick={() => { setSelectedCampaign(null); setMessages([]); }} style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer' }}>
                    <ArrowLeft size={18} />
                  </button>
                  <h2 style={{ margin: 0 }}>{selectedCampaign.name}</h2>
                  <span style={{ padding: '0.25rem 0.75rem', borderRadius: '20px', fontSize: '0.75rem', fontWeight: 600, background: `${statusColor(selectedCampaign.status)}22`, color: statusColor(selectedCampaign.status) }}>
                    {selectedCampaign.status}
                  </span>
                </div>
                <p style={{ color: '#94a3b8', fontSize: '0.85rem', marginTop: '0.5rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  {channelIcon(selectedCampaign.channel)} {selectedCampaign.channel} campaign
                  {selectedCampaign.segment_filter && ` | Segment: ${selectedCampaign.segment_filter}`}
                </p>
              </div>
              <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                <button className="btn-primary" onClick={() => handleGenerate(selectedCampaign.id)} disabled={generating} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  {generating ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
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
                { label: 'Total Leads', value: selectedCampaign.total_leads, icon: <Users size={20} />, color: '#6366f1' },
                { label: 'Pending', value: messageStats.pending || 0, icon: <Send size={20} />, color: '#94a3b8' },
                { label: 'Sent', value: messageStats.sent || 0, icon: <CheckCircle size={20} />, color: '#10b981' },
                { label: 'Replied', value: messageStats.replied || 0, icon: <Mail size={20} />, color: '#f59e0b' },
              ].map((stat, i) => (
                <div key={i} className="card" style={{ textAlign: 'center', padding: '1.25rem' }}>
                  <div style={{ color: stat.color, marginBottom: '0.5rem' }}>{stat.icon}</div>
                  <div style={{ fontSize: '1.5rem', fontWeight: 800 }}>{stat.value}</div>
                  <div style={{ fontSize: '0.8rem', color: '#94a3b8' }}>{stat.label}</div>
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
                      borderBottom: '1px solid rgba(255,255,255,0.05)',
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
                            background: msg.status === 'sent' ? 'rgba(16,185,129,0.1)' : 'rgba(148,163,184,0.1)',
                            color: msg.status === 'sent' ? '#10b981' : '#94a3b8'
                          }}>
                            {msg.status}
                          </span>
                        </div>
                        {msg.subject && <div style={{ fontSize: '0.8rem', color: '#e2e8f0' }}>{msg.subject}</div>}
                        <div style={{ fontSize: '0.75rem', color: '#64748b', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {msg.body}
                        </div>
                      </div>
                      <button
                        onClick={() => setPreviewMessage(msg)}
                        style={{ background: 'rgba(255,255,255,0.05)', border: 'none', borderRadius: '8px', padding: '0.5rem', color: '#94a3b8', cursor: 'pointer' }}
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
          <div style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
            backdropFilter: 'blur(4px)'
          }} onClick={() => setPreviewMessage(null)}>
            <div className="card" style={{ maxWidth: '600px', width: '90%', maxHeight: '80vh', overflowY: 'auto' }} onClick={e => e.stopPropagation()}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                <h3 style={{ margin: 0 }}>Message Preview</h3>
                <button onClick={() => setPreviewMessage(null)} style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer' }}>
                  <X size={20} />
                </button>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                <div>
                  <span style={{ fontSize: '0.75rem', color: '#64748b' }}>Channel</span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    {channelIcon(previewMessage.channel)} {previewMessage.channel}
                  </div>
                </div>
                <div>
                  <span style={{ fontSize: '0.75rem', color: '#64748b' }}>Lead</span>
                  <div>{previewMessage.lead_unique_key}</div>
                </div>
                {previewMessage.subject && (
                  <div>
                    <span style={{ fontSize: '0.75rem', color: '#64748b' }}>Subject</span>
                    <div style={{ fontWeight: 600 }}>{previewMessage.subject}</div>
                  </div>
                )}
                <div>
                  <span style={{ fontSize: '0.75rem', color: '#64748b' }}>Body</span>
                  <div style={{
                    background: 'rgba(255,255,255,0.03)',
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
                <p style={{ color: '#94a3b8', marginTop: '1rem' }}>Loading campaigns...</p>
              </div>
            ) : campaigns.length === 0 ? (
              <div className="card" style={{ textAlign: 'center', padding: '4rem' }}>
                <Mail size={48} style={{ color: '#64748b', marginBottom: '1rem' }} />
                <h3 style={{ color: '#e2e8f0', marginBottom: '0.5rem' }}>No Campaigns Yet</h3>
                <p style={{ color: '#94a3b8', marginBottom: '1.5rem' }}>Create your first outreach campaign to start reaching leads.</p>
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
                    onClick={() => { setSelectedCampaign(camp); fetchCampaignDetails(camp.id); }}
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
                          <span style={{ fontSize: '0.8rem', color: '#64748b' }}>Segment: {camp.segment_filter}</span>
                        )}
                      </div>
                      <div style={{ display: 'flex', gap: '2rem', fontSize: '0.85rem', color: '#94a3b8' }}>
                        <div><strong style={{ color: '#e2e8f0' }}>{camp.total_leads}</strong> leads</div>
                        <div><strong style={{ color: '#10b981' }}>{camp.sent_count}</strong> sent</div>
                        <div><strong style={{ color: '#f59e0b' }}>{camp.reply_count}</strong> replies</div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
