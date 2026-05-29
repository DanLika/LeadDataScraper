'use client';

import { useState, useRef, useEffect } from 'react';
import { Shield, Loader2, Play, Send, Copy, Check } from 'lucide-react';
import { API_BASE_URL, apiFetch } from '@/app/lib/apiConfig';

interface AIChatPlan {
  task: string;
  params: Record<string, string | number | boolean>;
  reasoning?: string;
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
  plan?: AIChatPlan;
}

interface AIChatProps {
  onExecute?: (plan: AIChatPlan) => Promise<Record<string, unknown>>;
  sidebarCollapsed?: boolean;
  hidden?: boolean;
}

export default function AIChat({ onExecute, sidebarCollapsed, hidden }: AIChatProps) {
  const [query, setQuery] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isMinimized, setIsMinimized] = useState(false);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!query.trim() || isLoading) return;

    const userMessage = query.trim();
    setMessages(prev => [...prev, { role: 'user', content: userMessage }]);
    setQuery('');
    setIsLoading(true);

    try {
      const response = await apiFetch(`${API_BASE_URL}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction: { text: userMessage } }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        // Pydantic 422 returns { detail: [{type, loc, msg, ...}] }. Surface the
        // first msg so the user sees "String should have at most 4000
        // characters" instead of a generic placeholder.
        const detail = Array.isArray(data?.detail)
          ? data.detail.map((d: { msg?: string }) => d?.msg).filter(Boolean).join('; ')
          : (data?.detail || data?.error || `Request failed (HTTP ${response.status})`);
        setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${detail}` }]);
        return;
      }
      if (data.error) {
        setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${data.error}` }]);
      } else {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: data.response || "I've analyzed your request.",
          plan: data.plan
        }]);
      }
    } catch (err) {
      console.error('AI query failed:', err);
      setMessages(prev => [...prev, { role: 'assistant', content: 'Sorry, I encountered an error while processing your request.' }]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleExecute = async (plan: AIChatPlan, index: number) => {
    if (onExecute) {
      setIsLoading(true);
      try {
        const result = await onExecute(plan);
        // Remove the plan from the message
        const newMessages = messages.map((msg, i) =>
          i === index ? { ...msg, plan: undefined } : msg
        );

        // Add a result message
        let content = "Task executed successfully.";
        if (result?.message) content = String(result.message);
        if (result?.draft) content = `Generated draft for ${String(result.lead_name || 'Prospect')}. I've opened the preview for you.`;
        if (result?.answer) content = String(result.answer);
        if (result?.details) content = `Status Check: ${JSON.stringify(result.details)}`;

        newMessages.push({ role: 'assistant', content });
        setMessages(newMessages);
      } catch {
        setMessages(prev => [...prev, { role: 'assistant', content: "Failed to execute the task." }]);
      } finally {
        setIsLoading(false);
      }
    }
  };

  const [windowWidth, setWindowWidth] = useState(1024);

  useEffect(() => {
    setWindowWidth(window.innerWidth);
    let timeout: NodeJS.Timeout;
    const handleResize = () => {
      clearTimeout(timeout);
      timeout = setTimeout(() => setWindowWidth(window.innerWidth), 150);
    };
    window.addEventListener('resize', handleResize);
    return () => { window.removeEventListener('resize', handleResize); clearTimeout(timeout); };
  }, []);

  const isMobile = windowWidth < 768;
  const isTablet = windowWidth >= 768 && windowWidth < 1024;

  if (hidden) return null;

  if (isMinimized) {
    return (
      <button 
        onClick={() => setIsMinimized(false)}
        aria-label="Open AI chat"
        style={{ 
          position: 'fixed', 
          bottom: isMobile ? '1rem' : '2rem', 
          right: isMobile ? '1rem' : '2.5rem', 
          width: '56px', 
          height: '56px', 
          borderRadius: '50%', 
          background: 'var(--primary)',
          color: 'var(--text-white)',
          display: 'flex', 
          alignItems: 'center', 
          justifyContent: 'center', 
          boxShadow: '0 10px 25px hsla(var(--primary-hsl), 0.4)',
          border: 'none',
          cursor: 'pointer',
          zIndex: 'var(--z-chat)'
        }}
      >
        <Shield size={24} />
      </button>
    );
  }

  return (
    <div role="region" aria-label="AI assistant" style={{
      position: 'fixed',
      bottom: isMobile ? '1rem' : '2rem',
      left: isMobile ? '1rem' : (isTablet ? '100px' : (sidebarCollapsed ? '100px' : '300px')),
      right: isMobile ? '1rem' : '2.5rem',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      zIndex: 'var(--z-chat)',
      transition: 'all 0.3s ease'
    }}>
      <div style={{ 
        width: '100%', 
        maxWidth: '850px', 
        background: 'var(--surface-elevated)',
        border: '1px solid var(--border)',
        borderRadius: '20px',
        boxShadow: '0 20px 50px -12px rgba(0, 0, 0, 0.4)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        transition: 'all 0.3s ease'
      }}>
        {/* Chat History */}
        {messages.length > 0 && (
          <div style={{ 
            maxHeight: '400px', 
            overflowY: 'auto', 
            padding: '1.5rem 2rem', 
            display: 'flex', 
            flexDirection: 'column', 
            gap: '1.5rem',
            borderBottom: '1px solid var(--surface-muted)',
            scrollbarWidth: 'thin',
            scrollbarColor: 'var(--border-muted) transparent'
          }}>
            {messages.map((msg, idx) => (
              <div key={idx} style={{ 
                display: 'flex', 
                flexDirection: 'column', 
                alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
                gap: '0.5rem'
              }}>
                <div style={{ 
                  background: msg.role === 'user' ? 'var(--surface-muted)' : 'hsla(var(--primary-hsl), 0.1)',
                  padding: msg.role === 'assistant' ? '1rem 2.5rem 1rem 1.25rem' : '1rem 1.25rem',
                  borderRadius: msg.role === 'user' ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
                  maxWidth: '80%',
                  fontSize: '0.95rem',
                  lineHeight: 1.5,
                  color: msg.role === 'user' ? 'var(--text-primary)' : 'var(--text-white)',
                  border: msg.role === 'user' ? '1px solid var(--border-muted)' : '1px solid hsla(var(--primary-hsl), 0.2)',
                  position: 'relative'
                }}>
                  {msg.content}
                  
                  {msg.role === 'assistant' && (
                    <button 
                      onClick={() => {
                        navigator.clipboard.writeText(msg.content);
                        setCopiedIdx(idx);
                        setTimeout(() => setCopiedIdx(null), 2000);
                      }}
                      aria-label="Copy message"
                      style={{
                        position: 'absolute',
                        top: '0.5rem',
                        right: '0.5rem',
                        background: 'var(--surface-muted)',
                        border: 'none',
                        borderRadius: '6px',
                        minWidth: '44px',
                        minHeight: '44px',
                        padding: '6px',
                        color: copiedIdx === idx ? 'var(--success-light)' : 'var(--text-muted)',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center'
                      }}
                      title="Copy to clipboard"
                    >
                      {copiedIdx === idx ? <Check size={14} /> : <Copy size={14} />}
                    </button>
                  )}
                </div>
                
                {msg.plan && (
                  <div style={{ 
                    marginTop: '0.5rem', 
                    background: 'hsla(var(--primary-hsl), 0.1)',
                    padding: '1rem',
                    borderRadius: '12px',
                    border: '1px solid hsla(var(--primary-hsl), 0.3)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '0.75rem',
                    width: '100%',
                    maxWidth: isMobile ? '100%' : '400px'
                  }}>
                    <div data-testid="plan-card" style={{ fontSize: '0.75rem', color: 'var(--primary-light)', fontWeight: 600, textTransform: 'uppercase' }}>Proposed Plan</div>
                    <div style={{ fontSize: '0.85rem', color: 'var(--text-primary)' }}>Task: <strong data-testid="plan-task">{msg.plan.task}</strong></div>
                    {msg.plan.params && Object.keys(msg.plan.params).length > 0 && (
                      <div data-testid="plan-params" style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                        Params: <code style={{ background: 'var(--surface-muted)', padding: '0.1rem 0.4rem', borderRadius: '4px', fontSize: '0.75rem' }}>{JSON.stringify(msg.plan.params)}</code>
                      </div>
                    )}
                    {msg.plan.reasoning && (
                      <div data-testid="plan-reasoning" style={{ fontSize: '0.8rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
                        {msg.plan.reasoning}
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: '0.5rem' }}>
                      <button
                        className="btn-primary"
                        style={{ padding: '0.4rem 0.75rem', fontSize: '0.75rem', display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}
                        onClick={() => msg.plan && handleExecute(msg.plan, idx)}
                        disabled={isLoading}
                        aria-busy={isLoading}
                      >
                        {isLoading
                          ? <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                          : <Play size={12} aria-hidden="true" />
                        }
                        {isLoading ? 'Executing…' : 'Confirm & Execute'}
                      </button>
                      <button
                        className="btn-secondary"
                        style={{ padding: '0.4rem 0.75rem', fontSize: '0.75rem' }}
                        onClick={() => {
                          setMessages(messages.map((msg, i) =>
                            i === idx ? { ...msg, plan: undefined } : msg
                          ));
                        }}
                        disabled={isLoading}
                      >
                        Dismiss
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}

        {/* Input Bar */}
        <form onSubmit={handleSubmit} aria-busy={isLoading} style={{
          padding: '1rem 2rem',
          display: 'flex',
          alignItems: 'center',
          gap: '1.25rem'
        }}>
          <button
            type="button"
            aria-label={isLoading ? "AI is processing" : "Clear chat history"}
            title={isLoading ? "AI is processing" : "Clear chat history"}
            style={{
              background: isLoading ? 'var(--primary)' : 'hsla(var(--primary-hsl), 0.2)',
              borderRadius: '10px',
              padding: '0.5rem',
              minWidth: '44px',
              minHeight: '44px',
              transition: 'background 0.3s',
              cursor: isLoading ? 'default' : 'pointer',
              border: 'none',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }} onClick={() => !isLoading && messages.length > 0 && setMessages([])}>
             {isLoading ? <Loader2 className="animate-spin" size={20} color="white" /> : <Shield size={20} color="white" />}
          </button>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask AI to audit, find emails, or filter leads..."
            aria-label="Ask the AI assistant"
            style={{ flex: 1, background: 'none', border: 'none', color: 'var(--text-white)', outline: 'none', fontSize: '1.1rem' }}
            disabled={isLoading}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
            <kbd style={{ background: 'var(--border-muted)', padding: '0.25rem 0.5rem', borderRadius: '4px', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
              ENTER
            </kbd>
            <button
              type="submit"
              disabled={!query.trim() || isLoading}
              aria-busy={isLoading}
              aria-label="Send message"
              title="Send message (Enter)"
              style={{
                background: 'none',
                border: 'none',
                color: query.trim() ? 'var(--primary)' : 'var(--text-dim)',
                cursor: 'pointer',
                transition: 'color 0.2s',
                minWidth: '44px',
                minHeight: '44px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center'
              }}
            >
              <Send size={20} aria-hidden="true" />
            </button>
          </div>
        </form>
      </div>
      
      {/* Floating Controls */}
      <div style={{ 
        marginTop: '0.75rem', 
        marginBottom: isMobile ? '0.5rem' : '0',
        display: 'flex', 
        gap: '0.5rem' 
      }}>
         <button
           onClick={() => setMessages([])}
           aria-label="Clear chat"
           style={{ background: 'var(--surface-muted)', border: 'none', borderRadius: '20px', padding: '0.5rem 1rem', minHeight: '44px', color: 'var(--text-muted)', fontSize: '0.8rem', cursor: 'pointer' }}
         >
           Clear Chat
         </button>
         <button
           onClick={() => setIsMinimized(true)}
           aria-label="Minimize AI chat"
           style={{ background: 'var(--surface-muted)', border: 'none', borderRadius: '20px', padding: '0.5rem 1rem', minHeight: '44px', color: 'var(--text-muted)', fontSize: '0.8rem', cursor: 'pointer' }}
         >
           Minimize
         </button>
      </div>
    </div>
  );
}
