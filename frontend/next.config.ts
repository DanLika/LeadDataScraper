import type { NextConfig } from "next";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
// Build a CSP that lets Next.js run (inline styles + dev HMR) and lets the
// browser talk to Supabase auth/realtime; everything else is blocked.
const cspDirectives = [
  "default-src 'self'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "object-src 'none'",
  // Next inlines style chunks; allow style-src 'self' + 'unsafe-inline'. CSS
  // is the only inline asset; scripts run from /_next/static (same-origin).
  "style-src 'self' 'unsafe-inline'",
  // 'unsafe-eval' is required by Next dev HMR; drop it in production builds.
  process.env.NODE_ENV === "production"
    ? "script-src 'self'"
    : "script-src 'self' 'unsafe-eval' 'unsafe-inline'",
  // Restrict images to same-origin + inline (data:/blob:) + the Supabase
  // project host (used for avatars / storage). Blanket `https:` would let a
  // future feature that renders attacker-controlled URLs exfil the viewer's
  // IP + Referer via tracking-pixel requests.
  `img-src 'self' data: blob: ${SUPABASE_URL}`.trim(),
  "font-src 'self' data:",
  // /api/proxy is same-origin; Supabase auth/realtime is the only cross-origin call.
  `connect-src 'self' ${SUPABASE_URL} ${SUPABASE_URL.replace(/^https:/, "wss:")}`.trim(),
].filter(Boolean);

const baseHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Content-Security-Policy", value: cspDirectives.join("; ") },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];
// HSTS is gated to production. In dev (localhost/HTTP) the directive is
// either ignored or, if served briefly over HTTPS, pins the hostname for two
// years — annoying when local tooling certificates change.
if (process.env.NODE_ENV === "production") {
  baseHeaders.push({
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  });
}

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [{ source: "/(.*)", headers: baseHeaders }];
  },
  productionBrowserSourceMaps: false,
};

export default nextConfig;
