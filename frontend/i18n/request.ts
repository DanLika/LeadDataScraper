// Server-side request config — resolves the locale from the
// `NEXT_LOCALE` cookie on every render and loads its messages JSON.
// Called by next-intl's webpack plugin (see next.config.ts).
//
// Returns `defaultLocale` if the cookie is missing or carries an
// unknown value — fail-open to English, never throw.

import { getRequestConfig } from 'next-intl/server';
import { cookies } from 'next/headers';
import { defaultLocale, isLocale, LOCALE_COOKIE } from './routing';

export default getRequestConfig(async () => {
  const store = await cookies();
  const cookieLocale = store.get(LOCALE_COOKIE)?.value;
  const locale = isLocale(cookieLocale) ? cookieLocale : defaultLocale;
  return {
    locale,
    // Dynamic import path — bundled at build time per locale, only the
    // matching file ships to the client per request.
    messages: (await import(`../messages/${locale}.json`)).default,
  };
});
