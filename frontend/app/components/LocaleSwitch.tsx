'use client';

import { useLocale, useTranslations } from 'next-intl';
import { useRouter } from 'next/navigation';
import { useTransition } from 'react';
import { Globe } from 'lucide-react';

const LOCALES = ['en', 'hr'] as const;
type Locale = (typeof LOCALES)[number];

function isLocale(value: string): value is Locale {
  return (LOCALES as readonly string[]).includes(value);
}

export default function LocaleSwitch({ compact = false }: { compact?: boolean }) {
  const current = useLocale();
  const router = useRouter();
  const t = useTranslations('locale');
  const [pending, startTransition] = useTransition();

  function select(next: Locale) {
    if (next === current) return;
    // Year-long persistence; SameSite=Lax matches Supabase session cookie
    // floor; Secure in prod only (httpOnly cannot be set from JS — fine,
    // this is a non-secret UI preference).
    const secure = typeof window !== 'undefined' && window.location.protocol === 'https:' ? '; Secure' : '';
    document.cookie = `NEXT_LOCALE=${next}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
    startTransition(() => {
      router.refresh();
    });
  }

  const labels: Record<Locale, string> = {
    en: t('english'),
    hr: t('croatian'),
  };

  if (compact) {
    const other: Locale = current === 'en' ? 'hr' : 'en';
    return (
      <button
        type="button"
        onClick={() => isLocale(other) && select(other)}
        disabled={pending}
        aria-label={`${t('switch_label')}: ${labels[other]}`}
        title={labels[other]}
        className="nav-item"
        style={{ opacity: pending ? 0.6 : 1 }}
      >
        <Globe size={18} aria-hidden="true" />
        <span>{labels[isLocale(current) ? current : 'en'].toUpperCase()}</span>
      </button>
    );
  }

  return (
    <div role="group" aria-label={t('switch_label')} style={{ display: 'flex', gap: '0.25rem' }}>
      {LOCALES.map((loc) => (
        <button
          key={loc}
          type="button"
          onClick={() => select(loc)}
          disabled={pending || loc === current}
          aria-pressed={loc === current}
          className={`nav-item ${loc === current ? 'active' : ''}`}
          style={{ flex: 1, justifyContent: 'center' }}
        >
          {labels[loc]}
        </button>
      ))}
    </div>
  );
}
