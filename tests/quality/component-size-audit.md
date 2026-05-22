# Component Size Audit

Sweep date: **2026-05-22**
Branch: `chore/component-size-audit` (base `origin/main` @ `ee2fa0c`)
Thresholds (per the operator's brief):
- `frontend/app/page.tsx` — **< 500 LOC**
- Any component file — **≤ 300 LOC**
- Custom hooks (`useFoo`) — **≤ 100 LOC**
- Inline JSX (single `return (...)` block) — **≤ 50 LOC**

Pattern advocated: **container vs presentational split** — containers
own state/effects/fetches; presentationals are pure `props → JSX`.

## Headline

| Threshold | Violations | Worst |
|---|---:|---|
| `page.tsx` < 500 LOC | **1** | `app/page.tsx` — **1 718 LOC** (3.4× the cap) |
| component ≤ 300 LOC | **5** | `app/page.tsx` (above) + 4 more |
| hooks ≤ 100 LOC | **0** | both shipped hooks fit comfortably |
| inline JSX ≤ 50 LOC | **5** (1 per oversized component) | `app/page.tsx` — single render block ≈ **881 LOC** |

## Caveat — scope is `origin/main`

The audit reflects the code currently on `origin/main`. The operator's
Faza 4 work (13 unreleased commits + a handful of untracked files) has
**already extracted** several heavy components:

- `frontend/app/components/LeadTable.tsx` (348 LOC, virtualised) —
  not on `origin/main`, lives in the local working tree
- `frontend/app/components/InsightsCharts.tsx` — same
- `frontend/app/components/WebVitalsReporter.tsx` — same
- `frontend/app/components/OfflineBanner.tsx` — same

When the Faza 4 batch lands, re-run this audit. Several `page.tsx` /
`insights/page.tsx` numbers will already be lower.

---

## Component inventory

| File | LOC | Status | Hooks density | Top concerns owned |
|---|---:|---|---:|---|
| `app/page.tsx` | **1 718** | ❌❌❌ way over | 27 hooks | Dashboard state machine, filter URL sync, cursor pagination, modal slots, lead-action handlers, offline queue wiring, cross-page bridge consume/strip |
| `app/campaigns/page.tsx` | **550** | ❌ over (≥ 300) | est. 8-10 | List view, creation form, send action, multi-channel preview |
| `app/components/AIChat.tsx` | **382** | ❌ over | est. 7 | Layout, message history, composer, plan-card render, execute-plan diff |
| `app/components/Sidebar.tsx` | **362** | ❌ over | est. 5 | Nav structure, mobile drawer transform, insights widget, cross-page setter shims |
| `app/insights/page.tsx` | **345** | ❌ over | est. 6 | Insights fetch, 4 chart panels, AI strategic analysis section |
| `app/api/proxy/[...path]/route.ts` | 213 | ⚠️ API route (not a component) — borderline | — | Documented separately in long-functions-report.md |
| `app/login/page.tsx` | 132 | ✅ fine | 2 | Login form + state machine |
| `app/components/HealthChart.tsx` | 97 | ✅ fine | — | PieChart + stats grid |
| `app/components/FilterBar.tsx` | 89 | ✅ fine | — | Search / segment / status / score controls |
| `utils/supabase/middleware.ts` | 85 | ✅ fine (security-critical, leave alone) | — | Cookie floor + auth gate |
| `utils/loginThrottle.ts` | 84 | ✅ fine | — | Per-IP 5/60s bucket |
| `app/login/actions.ts` | 77 | ✅ fine | — | Server actions + `sanitizeNext` glue |
| `utils/useFocusTrap.ts` | 67 | ✅ hook fine | — | A11y focus trap |
| `app/components/StatsCards.tsx` | 64 | ✅ fine (per-card extraction noted in duplication report) | — | 4 summary cards |
| `utils/useEscape.ts` | 54 | ✅ hook fine | — | ESC keydown handler |
| `app/components/BrandIcons.tsx` | 46 | ✅ fine | — | SVG icons |
| `app/layout.tsx` | 21 | ✅ fine | — | Root layout + WebVitalsReporter mount |

---

## Inline JSX block sizes (single `return (...)`)

Each oversized component has a **single render block** that itself
exceeds the 50-LOC subcomponent threshold by a wide margin:

| File | Render block | Notes |
|---|---:|---|
| `app/page.tsx` `DashboardInner` | L837–end ≈ **881 LOC** | One return; further nested `.map`-callback render at L1 259 |
| `app/campaigns/page.tsx` `CampaignsPage` | L217–end ≈ **334 LOC** | Single return |
| `app/insights/page.tsx` `InsightsPage` | L110–end ≈ **235 LOC** | Plus a 7-LOC early `return` for loading at L103 |
| `app/components/Sidebar.tsx` `Sidebar` | L108–end ≈ **254 LOC** | Two nested `.map`-callback renders at L256 + L319 (nav items / insights widget rows) |
| `app/components/AIChat.tsx` `AIChat` | L156–end ≈ **226 LOC** | Plus a 27-LOC early `return` for hidden state at L129 |

All five are textbook container-with-monster-render. The container-side
state + handler code is reasonable (~64-178 LOC each except `page.tsx`).
The JSX is what's out of hand.

---

## Per-component extraction plan (container ↔ presentational)

### 1. `app/page.tsx` `DashboardInner` — 1 718 LOC, 27 hooks, 881-LOC render

**Highest leverage, highest risk** — no unit tests, only Playwright E2E
governs the page-level integration. Mentioned by the operator: "The
huge page.tsx (~3000 LOC pre-Faza 4) likely still has functions to
split" — confirmed.

Multi-PR test-first effort. Container shrinks as each extraction lands.

**Hooks to extract** (container concerns lifted to reusable hooks):

| Custom hook | Owns | LOC saved on `page.tsx` |
|---|---|---:|
| `useLeadFilters()` | `view`/`searchTerm`/`segment`/`status`/`minScore`/`sort` state + the URL query-param sync (consume `?view=`, `?q=`, `?segment=`, `?status=`, `?min=`, `?sort=` → state; write back on change) | ≈ 120-180 |
| `useCursorPaginatedLeads()` | `leads`/`nextCursor`/`hasMore` state + `loadMoreLeads` fetch + `fetch /leads?limit&cursor` plumbing + 401-handling for the offline queue | ≈ 100-150 |
| `useCrossPageBridge()` | `?openSettings=1`/`?openDiscovery=1`/`?view=`/`?search=` consume-then-strip from the URL on mount (the cross-page nav contract in CLAUDE.md) | ≈ 40-60 |
| `useDashboardStats()` | `/stats` fetch + 60s refresh interval + abort-on-unmount | ≈ 40 |

**Presentational components to extract** (pure props → JSX):

| Component | Owns | LOC moved off `page.tsx` |
|---|---|---:|
| `<DashboardHeader>` | logo + page-title + filter-summary chips | ≈ 80 |
| `<DashboardActions>` | "Process all" / "Hunt all" / "Discovery" / "Settings" button cluster + confirm dialogs | ≈ 100 |
| `<SettingsModal>` | Settings dialog body + ESC handler + save handler (uses `useFocusTrap`) | ≈ 150 |
| `<DiscoveryModal>` | Deep-discovery dialog body + same a11y treatment | ≈ 150 |
| `<OutreachModal>` | Outreach draft modal + Gmail mailto link (the `encodeURIComponent`-wrapped `leadEmail` invariant from CLAUDE.md MUST be preserved verbatim — security-critical) | ≈ 100 |
| `<LeadActionConfirm>` | Confirm dialog for destructive actions (Clear all / Bulk process) | ≈ 60 |

After: `DashboardInner` ≈ 250-350 LOC of container glue (hook calls + prop wiring + 1 small render block delegating to the above). Under the 500-LOC target.

### 2. `app/campaigns/page.tsx` `CampaignsPage` — 550 LOC

**Mid risk, mid effort.** No unit tests; covered by E2E specs.

| Extraction | Concern |
|---|---|
| `<CampaignList>` | Table of campaigns + status filter |
| `<CampaignCreateForm>` | Create flow (name, channel, segment_filter) + POST |
| `<CampaignDetailPane>` | Selected-campaign side panel with stats + messages preview |
| `<CampaignSendButton>` | "Start" / "Pause" / "Generate" action cluster |

Pairs with PR #192's backend layered split: the page component should
call `apiFetch('/api/proxy/campaigns/<id>/start')` via small handler
functions, with the JSX simply binding `onClick` props.

Per duplication report item D: the page also shares a 43-LOC
`<Sidebar>` shim block with `insights/page.tsx` — extract `<NavShell>`
when a 3rd non-dashboard page joins.

### 3. `app/components/AIChat.tsx` `AIChat` — 382 LOC

Currently a single component owning floating-chat layout + message
history + form + plan-card render + execute-plan diff.

| Extraction | Concern |
|---|---|
| `<ChatLauncher>` | Floating button + open/closed transform |
| `<ChatPanel>` | The drawer body wrapper |
| `<ChatMessageList>` | Virtualised-ish message history (currently a flat list — keep flat, the chat won't have 1000-row history) |
| `<ChatComposer>` | Textarea + send + char counter; honours the 4000-char Pydantic limit from CLAUDE.md |
| `<PlanCard>` | The "Confirm & Execute" UI; preserves the `reasoning`-stripping logic before POST (`/execute` rejects extra fields per `extra='forbid'`) |

A `useChatHistory()` hook holds the message array + scroll-to-bottom on
new-message effect.

### 4. `app/components/Sidebar.tsx` `Sidebar` — 362 LOC

The 14-prop API noted in the duplication report (one prop per consumer
of dashboard state) is itself a smell — the cross-page bridge prop
pattern would simplify when `useCrossPageBridge()` (from `page.tsx`
above) becomes a reusable hook.

| Extraction | Concern |
|---|---|
| `<NavList>` | The vertical nav items + active-route highlight |
| `<MobileDrawer>` | The mobile slide-in wrapper + backdrop + ESC trap (already uses `useFocusTrap`) |
| `<InsightsWidget>` | The collapsible insights summary — its own component because it fetches lazily |

### 5. `app/insights/page.tsx` `InsightsPage` — 345 LOC

Pairs with the lazy-loaded `InsightsCharts` already extracted in the
Faza 4 batch. Remaining over-300 surface is the strategic analysis
section + the `<Sidebar>` shim.

| Extraction | Concern |
|---|---|
| `<StrategicAnalysisPanel>` | The Gemini-fed insights summary + risk signals + next-actions render |
| `<InsightsHeader>` | Page title + refresh action |
| Re-use `<NavShell>` (or `<Sidebar>` directly with the bridge hook) | Eliminates the 43-LOC shim duplicated with `campaigns/page.tsx` |

---

## Hooks audit

Both shipped hooks pass the 100-LOC threshold and have a single clear
concern:

| Hook | LOC | Concern | Verdict |
|---|---:|---|---|
| `utils/useFocusTrap.ts::useFocusTrap` | 67 | A11y focus trap for modals (Tab/Shift-Tab cycle, initial-focus, restore-focus) | ✅ keep |
| `utils/useEscape.ts::useEscape` | 54 | ESC keydown handler with `active` gating | ✅ keep |

**Gap**: `useFocusTrap` + `useEscape` are the only custom hooks in the
repo. Every other component owns its own `useEffect`-based cleanup
patterns inline. The Phase 1 extractions on `DashboardInner` will
introduce ~4 new hooks (`useLeadFilters`, `useCursorPaginatedLeads`,
`useCrossPageBridge`, `useDashboardStats`) — those will be the first
real reusable hooks in the repo. They belong in `frontend/utils/`
alongside the existing two.

---

## Container vs presentational pattern (target)

After the extractions above, the directory shape should look like:

```
frontend/app/
├── page.tsx                       (container; ≤ 500 LOC)
├── campaigns/page.tsx             (container; ≤ 300 LOC)
├── insights/page.tsx              (container; ≤ 300 LOC)
├── login/page.tsx                 (container; already fine)
├── components/
│   ├── DashboardHeader.tsx        (presentational)
│   ├── DashboardActions.tsx       (presentational)
│   ├── SettingsModal.tsx          (presentational + own ESC/focus trap)
│   ├── DiscoveryModal.tsx         (presentational)
│   ├── OutreachModal.tsx          (presentational; preserves `encodeURIComponent`-wrapped `leadEmail`)
│   ├── LeadTable.tsx              (already extracted; Faza 4.11)
│   ├── CampaignList.tsx           (presentational)
│   ├── CampaignCreateForm.tsx     (presentational + form state)
│   ├── CampaignDetailPane.tsx     (presentational)
│   ├── ChatLauncher.tsx           (presentational)
│   ├── ChatPanel.tsx              (presentational)
│   ├── ChatMessageList.tsx        (presentational)
│   ├── ChatComposer.tsx           (presentational + form state)
│   ├── PlanCard.tsx               (presentational; strips `reasoning` before POST)
│   ├── NavList.tsx                (presentational)
│   ├── MobileDrawer.tsx           (presentational + `useFocusTrap`)
│   ├── InsightsWidget.tsx         (presentational + own fetch)
│   ├── StrategicAnalysisPanel.tsx (presentational)
│   └── (existing) HealthChart, FilterBar, StatsCards, BrandIcons, Sidebar (slimmed), ...
└── hooks/                         (new directory? or stay in utils/)
    ├── useLeadFilters.ts
    ├── useCursorPaginatedLeads.ts
    ├── useCrossPageBridge.ts
    ├── useDashboardStats.ts
    └── useChatHistory.ts
```

**Note on hooks directory placement**: the existing `useFocusTrap` and
`useEscape` live in `frontend/utils/` (alongside `apiConfig.ts` and
`url.mjs`). When the new hooks land, either keep all hooks in
`frontend/utils/` (consistent with current convention) or migrate
everything to `frontend/app/hooks/` (more conventional Next.js layout).
The latter is more discoverable; the former matches existing convention.
**Default to existing convention** unless the operator wants the
migration.

---

## Recommended PR order

| Order | Target | Why first |
|---|---|---|
| 1 | `useLeadFilters()` hook extraction from `DashboardInner` | Pure state-machine refactor; testable in isolation; biggest single win (-150 LOC from `page.tsx`) |
| 2 | `useCursorPaginatedLeads()` hook | Similar shape; reuses the testable pattern from #1 |
| 3 | `<SettingsModal>` + `<DiscoveryModal>` + `<OutreachModal>` extraction | Modal slot pattern; preserves the `encodeURIComponent` security invariant; -400 LOC |
| 4 | `<CampaignDetailPane>` + `<CampaignCreateForm>` from `campaigns/page.tsx` | Brings campaigns under 300 LOC |
| 5 | `<ChatMessageList>` + `<ChatComposer>` + `<PlanCard>` from `AIChat.tsx` | Brings AIChat under 300 LOC; PlanCard preserves the reasoning-strip invariant |
| 6 | `<MobileDrawer>` + `<InsightsWidget>` from `Sidebar.tsx` | Brings Sidebar under 300 LOC |
| 7 | `<StrategicAnalysisPanel>` from `insights/page.tsx` | Brings insights under 300 LOC |
| 8 | `useCrossPageBridge()` + `useDashboardStats()` | Final hook extractions to slim `DashboardInner` to its container essence |
| 9 | `<NavShell>` (deferred until 3rd non-dashboard page; per duplication-report item D) | wait |

Estimated landing order: 1-4 are independent. 5-7 are independent.
8 depends on 1. Realistic cadence: 2-3 PRs per week if test-first.

---

## Security invariants to preserve verbatim during extraction

These are pinned in `CLAUDE.md` and must not be lost when JSX moves
into a presentational component:

1. **Outreach mailto** (`encodeURIComponent`-wrapped `leadEmail`): when
   `<OutreachModal>` is extracted, every interpolated email / subject /
   body MUST stay wrapped — preserves the open-redirect / header-smuggling
   defense.
2. **Plan card reasoning strip**: `<PlanCard>` must drop the `reasoning`
   field before POSTing to `/execute`. The backend's
   `ExecutePlanParams` is `extra='forbid'` — any leaked field 422s and
   the Confirm & Execute button silently fails.
3. **AIChat 422 detail unwrap**: `handleSubmit` joins
   `detail[].msg` on Pydantic 422 responses so users see "String should
   have at most 4000 characters" instead of a placeholder. When
   `<ChatComposer>` is extracted, the join behaviour stays with the
   container handler.
4. **Cross-page setter shims**: when `<Sidebar>` is decomposed, the
   `(open) => { if (open) router.push(...) }` shim pattern (not
   `setShowSettings(true)`) must survive. CLAUDE.md notes:
   `setShowDiscoveryModal(false)` would naively trigger
   `router.push('/?openDiscovery=1')` and open the wrong modal.

---

## Reproducing

```sh
# Per-file LOC
find frontend/app frontend/utils -type f \
  \( -name "*.tsx" -o -name "*.ts" -o -name "*.mjs" \) \
  ! -path "*/node_modules/*" ! -path "*/.next/*" \
  -exec wc -l {} + | sort -rn

# Custom hooks
grep -rnE "^export (default )?function use[A-Z]|^export const use[A-Z]" \
  frontend/app frontend/utils

# Eslint built-in for the future: add `max-lines-per-function` to the
# project's eslint.config.mjs once page.tsx is below 500 LOC, so
# regression breaks CI:
#   rules: { "max-lines-per-function": ["error", { max: 500, skipBlankLines: true }] }
```

## Weekly tracking

| Week of | page.tsx LOC | components > 300 | hooks > 100 | PR landed |
|---|---:|---:|---:|---|
| 2026-05-22 | 1 718 | 5 | 0 | — (baseline) |
