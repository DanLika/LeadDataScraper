'use client';

import { useLocale, useTranslations } from 'next-intl';
import { useRouter } from 'next/navigation';
import { useTransition } from 'react';

import { locales, type Locale } from '../../i18n/routing';

// Hardcoded labels mirror messages/{en,hr}.json `localeSwitcher.*`. Keeps
// the option labels in their own native script regardless of current
// locale (Italian-in-English UI says "English | Hrvatski" not
// "Inglese | Croato"), which is the common UX convention.
const NATIVE_LABEL: Record<Locale, string> = {
  en: 'English',
  hr: 'Hrvatski',
};

// Cookie path / SameSite / Max-Age all set explicitly so the cookie:
//   - is read by the next render (path=/)
//   - survives a tab close (Max-Age=1y)
//   - won't ride across navigations to a different site (SameSite=Lax)
//   - mirrors the existing cookie-floor stance for Supabase session cookies
// `secure` is set in prod only — `document.cookie` would silently refuse
// `Secure` over plain http://localhost in dev.
function persistLocaleCookie(locale: Locale) {
  const oneYearSeconds = 60 * 60 * 24 * 365;
  const isHttps = typeof window !== 'undefined' && window.location.protocol === 'https:';
  const secure = isHttps ? '; Secure' : '';
  document.cookie = `NEXT_LOCALE=${locale}; Max-Age=${oneYearSeconds}; Path=/; SameSite=Lax${secure}`;
}

export default function LocaleSwitcher() {
  const current = useLocale() as Locale;
  const t = useTranslations('localeSwitcher');
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  return (
    <label
      style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.8rem', color: 'var(--text-dim)' }}
    >
      <span className="sr-only-not-really" style={{ fontSize: '0.75rem' }}>
        {t('label')}
      </span>
      <select
        value={current}
        disabled={isPending}
        aria-label={t('label')}
        onChange={(e) => {
          const next = e.target.value as Locale;
          if (next === current) return;
          persistLocaleCookie(next);
          // router.refresh() re-runs the server render, which means
          // i18n/request.ts re-reads the cookie + ships the new messages.
          // No full window.location.reload() needed — preserves any
          // open modal / scroll position the operator was at.
          startTransition(() => router.refresh());
        }}
        style={{ background: 'var(--surface-muted)', color: 'var(--text-primary)', border: '1px solid var(--border-subtle)', borderRadius: '8px', padding: '0.25rem 0.5rem', fontSize: '0.8rem', cursor: isPending ? 'progress' : 'pointer' }}
      >
        {locales.map((l) => (
          <option key={l} value={l} style={{ background: 'var(--surface-dark)' }}>
            {NATIVE_LABEL[l]}
          </option>
        ))}
      </select>
    </label>
  );
}
