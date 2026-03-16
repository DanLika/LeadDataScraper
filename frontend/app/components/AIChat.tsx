'use client';

import { useState, useRef, useEffect } from 'react';
import { Shield, Loader2, Play, X, Send, Copy, Check } from 'lucide-react';
import { API_BASE_URL } from '@/utils/apiConfig';

interface Message {
  role: 'user' | 'assistant';
  content: string;
  plan?: {
    task: string;
    params: any;
  };
}

interface AIChatProps {
  onExecute?: (plan: any) => void;
  sidebarCollapsed?: boolean;
}

export default function AIChat({ onExecute, sidebarCollapsed }: AIChatProps) {
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
      const response = await fetch(`${API_BASE_URL}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction: { text: userMessage } }),
      });
      const data = await response.json();
      
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

  const handleExecute = async (plan: any, index: number) => {
    if (onExecute) {
      setIsLoading(true);
      try {
        const result = await (onExecute as any)(plan);
        // Remove the plan from the message
        const newMessages = messages.map((msg, i) =>
          i === index ? { ...msg, plan: undefined } : msg
        );

        // Add a result message
        let content = "Task executed successfully.";
        if (result?.message) content = result.message;
        if (result?.draft) content = `Generated draft for ${result.lead_name || 'Prospect'}. I've opened the preview for you.`;
        if (result?.answer) content = result.answer;
        if (result?.details) content = `Status Check: ${JSON.stringify(result.details)}`;

        newMessages.push({ role: 'assistant', content });
        setMessages(newMessages);
      } catch (err) {
        setMessages(prev => [...prev, { role: 'assistant', content: "Failed to execute the task." }]);
      } finally {
        setIsLoading(false);
      }
    }
  };

  const [windowWidth, setWindowWidth] = useState(typeof window !== 'undefined' ? window.innerWidth : 1200);

  useEffect(() => {
    const handleResize = () => setWindowWidth(window.innerWidth);
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  const isMobile = windowWidth < 768;
  const isTablet = windowWidth >= 768 && windowWidth < 1024;

  if (isMinimized) {
    return (
      <button 
        onClick={() => setIsMinimized(false)}
        style={{ 
          position: 'fixed', 
          bottom: isMobile ? '1rem' : '2rem', 
          right: isMobile ? '1rem' : '2.5rem', 
          width: '56px', 
          height: '56px', 
          borderRadius: '50%', 
          background: 'var(--primary)', 
          color: 'white', 
          display: 'flex', 
          alignItems: 'center', 
          justifyContent: 'center', 
          boxShadow: '0 10px 25px rgba(99, 102, 241, 0.4)',
          border: 'none',
          cursor: 'pointer',
          zIndex: 1000
        }}
      >
        <Shield size={24} />
      </button>
    );
  }

  return (
    <div style={{ 
      position: 'fixed', 
      bottom: isMobile ? '1rem' : '2rem', 
      left: isMobile ? '1rem' : (isTablet ? '100px' : (sidebarCollapsed ? '100px' : '300px')),
      right: isMobile ? '1rem' : '2.5rem', 
      display: 'flex', 
      flexDirection: 'column', 
      alignItems: 'center', 
      zIndex: 1000,
      transition: 'all 0.3s ease'
    }}>
      <div style={{ 
        width: '100%', 
        maxWidth: '850px', 
        background: 'rgba(15, 15, 18, 0.85)', 
        backdropFilter: 'blur(32px)', 
        border: '1px solid rgba(255,255,255,0.1)', 
        borderRadius: '28px',
        boxShadow: '0 40px 80px -12px rgba(0, 0, 0, 0.8), inset 0 1px 1px rgba(255,255,255,0.1)',
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
            borderBottom: '1px solid rgba(255,255,255,0.05)',
            scrollbarWidth: 'thin',
            scrollbarColor: 'rgba(255,255,255,0.1) transparent'
          }}>
            {messages.map((msg, idx) => (
              <div key={idx} style={{ 
                display: 'flex', 
                flexDirection: 'column', 
                alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
                gap: '0.5rem'
              }}>
                <div style={{ 
                  background: msg.role === 'user' ? 'rgba(255,255,255,0.05)' : 'rgba(99, 102, 241, 0.1)', 
                  padding: msg.role === 'assistant' ? '1rem 2.5rem 1rem 1.25rem' : '1rem 1.25rem',
                  borderRadius: msg.role === 'user' ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
                  maxWidth: '80%',
                  fontSize: '0.95rem',
                  lineHeight: 1.5,
                  color: msg.role === 'user' ? '#e2e8f0' : 'white',
                  border: msg.role === 'user' ? '1px solid rgba(255,255,255,0.1)' : '1px solid rgba(99, 102, 241, 0.2)',
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
                      style={{
                        position: 'absolute',
                        top: '0.5rem',
                        right: '0.5rem',
                        background: 'rgba(255,255,255,0.05)',
                        border: 'none',
                        borderRadius: '6px',
                        padding: '4px',
                        color: copiedIdx === idx ? '#4ade80' : '#94a3b8',
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
                    background: 'rgba(99, 102, 241, 0.1)', 
                    padding: '1rem', 
                    borderRadius: '12px', 
                    border: '1px solid rgba(99, 102, 241, 0.3)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '0.75rem',
                    width: '100%',
                    maxWidth: isMobile ? '100%' : '400px'
                  }}>
                    <div style={{ fontSize: '0.75rem', color: '#a5b4fc', fontWeight: 600, textTransform: 'uppercase' }}>Proposed Plan</div>
                    <div style={{ fontSize: '0.85rem', color: '#e2e8f0' }}>Task: <strong>{msg.plan.task}</strong></div>
                    <div style={{ display: 'flex', gap: '0.5rem' }}>
                      <button 
                        className="btn-primary" 
                        style={{ padding: '0.4rem 0.75rem', fontSize: '0.75rem' }}
                        onClick={() => handleExecute(msg.plan, idx)}
                      >
                        <Play size={12} /> Confirm & Execute
                      </button>
                      <button 
                        className="btn-secondary" 
                        style={{ padding: '0.4rem 0.75rem', fontSize: '0.75rem' }}
                        onClick={() => {
                          setMessages(messages.map((msg, i) =>
                            i === idx ? { ...msg, plan: undefined } : msg
                          ));
                        }}
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
        <form onSubmit={handleSubmit} style={{ 
          padding: '1rem 2rem',
          display: 'flex',
          alignItems: 'center',
          gap: '1.25rem'
        }}>
          <div style={{ 
            background: isLoading ? 'var(--primary)' : 'rgba(99, 102, 241, 0.2)', 
            borderRadius: '10px', 
            padding: '0.5rem', 
            transition: 'background 0.3s',
            cursor: 'pointer'
          }} onClick={() => messages.length > 0 && setMessages([])}>
             {isLoading ? <Loader2 className="animate-spin" size={20} color="white" /> : <Shield size={20} color="white" />}
          </div>
          <input 
            type="text" 
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask AI to audit, find emails, or filter leads..." 
            style={{ flex: 1, background: 'none', border: 'none', color: 'white', outline: 'none', fontSize: '1.1rem' }}
            disabled={isLoading}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
            <kbd style={{ background: 'rgba(255,255,255,0.1)', padding: '0.25rem 0.5rem', borderRadius: '4px', fontSize: '0.75rem', color: '#94a3b8' }}>
              ENTER
            </kbd>
            <button 
              type="submit"
              disabled={!query.trim() || isLoading}
              style={{ 
                background: 'none', 
                border: 'none', 
                color: query.trim() ? 'var(--primary)' : 'rgba(255,255,255,0.2)', 
                cursor: 'pointer',
                transition: 'color 0.2s'
              }}
            >
              <Send size={20} />
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
           style={{ background: 'rgba(255,255,255,0.05)', border: 'none', borderRadius: '20px', padding: '0.25rem 0.75rem', color: '#94a3b8', fontSize: '0.7rem', cursor: 'pointer' }}
         >
           Clear Chat
         </button>
         <button 
           onClick={() => setIsMinimized(true)}
           style={{ background: 'rgba(255,255,255,0.05)', border: 'none', borderRadius: '20px', padding: '0.25rem 0.75rem', color: '#94a3b8', fontSize: '0.7rem', cursor: 'pointer' }}
         >
           Minimize
         </button>
      </div>
    </div>
  );
}
