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
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  // /api/proxy is same-origin; Supabase auth/realtime is the only cross-origin call.
  `connect-src 'self' ${SUPABASE_URL} ${SUPABASE_URL.replace(/^https:/, "wss:")}`.trim(),
].filter(Boolean);

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-XSS-Protection", value: "1; mode=block" },
          { key: "Content-Security-Policy", value: cspDirectives.join("; ") },
          {
            key: "Strict-Transport-Security",
            value: "max-age=63072000; includeSubDomains; preload",
          },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=()",
          },
        ],
      },
    ];
  },
  productionBrowserSourceMaps: false,
};

export default nextConfig;
