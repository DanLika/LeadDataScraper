import './globals.css';
import type { Metadata } from 'next';
import { headers } from 'next/headers';
import { NextIntlClientProvider } from 'next-intl';
import { getLocale, getMessages } from 'next-intl/server';
import OfflineBanner from './components/OfflineBanner';
import WebVitalsReporter from './components/WebVitalsReporter';

export const metadata: Metadata = {
  title: 'LeadDataScraper | CRM & Audit Dashboard',
  description: 'AI-Powered Lead Generation and Website Auditing Tool',
};

// Opt the whole app into per-request rendering. Required for the CSP nonce
// pipeline in `frontend/proxy.ts` to take effect — Next 16 only auto-stamps
// nonces onto its inline `__next_f` bootstrap blocks when the page is
// rendered per-request and `headers().get('x-nonce')` is read somewhere in
// the tree. With the previous static prerender, the HTML was baked at build
// time with no nonce and CSP rejected hydration. The auth-gated dashboards
// are personalized anyway, so static prerender added no value.
export const dynamic = 'force-dynamic';

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Reading the nonce registers a `headers()` dependency, which combined
  // with `dynamic = 'force-dynamic'` makes Next.js stamp the same nonce on
  // every inline script it streams.
  const nonce = (await headers()).get('x-nonce') ?? undefined;

  // Per-request locale + messages. `getLocale()` consults i18n/request.ts
  // (cookie NEXT_LOCALE → defaultLocale fallback). `<html lang>` reflects
  // the active locale so screen readers + browser TTS pick the right
  // pronunciation, and `<NextIntlClientProvider>` exposes `useTranslations`
  // / `useFormatter` to every client component below.
  const locale = await getLocale();
  const messages = await getMessages();

  return (
    <html lang={locale}>
      <body data-nonce={nonce ? '1' : '0'}>
        <NextIntlClientProvider locale={locale} messages={messages}>
          <OfflineBanner />
          <WebVitalsReporter />
          {children}
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
