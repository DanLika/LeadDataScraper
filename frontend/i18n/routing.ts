// i18n routing config — cookie-only locale strategy (no URL form).
//
// LDS is an internal operator tool. The full bookbed-website
// next-intl setup (route-group `app/[locale]/`, middleware-driven
// detection, `localePrefix: 'as-needed'`) is intentionally NOT
// adopted here because:
//   - There are no public/SEO URLs to preserve.
//   - The existing `frontend/proxy.ts` middleware already runs CSP
//     nonce + Supabase auth and composing a second top-level
//     middleware (next-intl's `createMiddleware`) is non-trivial.
//   - The operator only ever uses one locale at a time; switching
//     via a cookie + reload is acceptable UX for the dogfood scope.
//
// Upgrade path (later, if SEO ever matters): adopt route-group +
// middleware composition exactly like bookbed-website. The
// `routing.ts` shape is intentionally compatible.

export const locales = ['en', 'hr'] as const;
export const defaultLocale: Locale = 'en';
export type Locale = (typeof locales)[number];

export const LOCALE_COOKIE = 'NEXT_LOCALE';

export function isLocale(value: string | null | undefined): value is Locale {
  if (!value) return false;
  return (locales as readonly string[]).includes(value);
}
