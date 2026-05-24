import { getRequestConfig } from 'next-intl/server';
import { cookies } from 'next/headers';

export const locales = ['en', 'hr'] as const;
export type Locale = (typeof locales)[number];
export const defaultLocale: Locale = 'en';

// Cookie-based locale: no URL prefix, no middleware composition. Goal is
// dogfood for a single Croatian operator, not public SEO. If LDS ever
// goes multi-tenant + commercial, revisit and adopt a route-group
// (`app/[locale]/`) + next-intl middleware combo at that point.
export const LOCALE_COOKIE = 'NEXT_LOCALE';

function isLocale(value: string | undefined): value is Locale {
  return value !== undefined && (locales as readonly string[]).includes(value);
}

export default getRequestConfig(async () => {
  const store = await cookies();
  const fromCookie = store.get(LOCALE_COOKIE)?.value;
  const locale: Locale = isLocale(fromCookie) ? fromCookie : defaultLocale;
  return {
    locale,
    messages: (await import(`../messages/${locale}.json`)).default,
  };
});
