# Frontend Technical Audit Report

**Date:** 2026-03-28
**Scope:** LeadDataScraper Frontend (Next.js + React 19)
**Files audited:** 8 source files (~2,800 lines of application code)

---

## Audit Health Score

| # | Dimension | Score | Key Finding |
|---|-----------|-------|-------------|
| 1 | Accessibility | 2/4 | Missing form labels, clickable divs not keyboard-accessible |
| 2 | Performance | 2/4 | Unmemoized computed values, no code splitting |
| 3 | Theming | 1/4 | 50+ hard-coded hex colors despite token system |
| 4 | Responsive Design | 3/4 | Strong breakpoints; several touch targets below 44px |
| 5 | Anti-Patterns | 2/4 | Heavy glassmorphism, AI color palette (indigo/purple/cyan) |
| **Total** | | **10/20** | **Acceptable (significant work needed)** |

---

## Anti-Patterns Verdict

**Verdict: Borderline AI aesthetic.** The design has 3 AI tells:

1. **Glassmorphism overuse** -- `backdrop-filter: blur()` on nearly every surface (cards, sidebar, mobile header, AI chat, modal backdrop). The glass-bg token is the default card background.
2. **AI color palette** -- Primary indigo (#6366f1), secondary purple (#a855f7), accent cyan. This is the canonical AI-generated palette.
3. **Hero metrics** -- 2.5rem/800-weight stat numbers on dashboard, `stat-value` at 1.75rem/800. Classic AI dashboard pattern.

Not present: gradient text, bounce easing, generic fonts (Inter is good). The design is functional and not egregiously AI-slop, but the glass + indigo combination is recognizable.

---

## Executive Summary

- **Audit Health Score: 10/20** (Acceptable)
- **Total issues: 23** (P0: 0, P1: 8, P2: 10, P3: 5)
- **Top 5 critical issues:**
  1. 50+ hard-coded hex colors bypass the design token system
  2. Discovery/filter form inputs missing associated labels (WCAG 1.3.1)
  3. Clickable `div` elements (prospect items, campaign cards) are not keyboard-accessible
  4. Several interactive elements below 44px touch target minimum
  5. `getHealthData()` and `filteredLeads` recalculated every render without memoization

---

## Detailed Findings by Severity

### P1 Major Issues

**[P1] Missing form labels on filter controls**
- **Location:** `app/page.tsx:900-932` (segment select, status select, score range input), `app/page.tsx:892-898` (search input)
- **Category:** Accessibility
- **Impact:** Screen readers cannot identify the purpose of these controls
- **WCAG:** 1.3.1 Info and Relationships, 4.1.2 Name Role Value
- **Recommendation:** Add `<label htmlFor="...">` elements or `aria-label` attributes to all filter controls
- **Suggested command:** `/harden`

**[P1] Discovery modal inputs missing label association**
- **Location:** `app/page.tsx:1218-1236` -- labels use `style` but no `htmlFor`; inputs have no `id`
- **Category:** Accessibility
- **Impact:** Labels are visual-only; clicking them won't focus the input, screen readers won't associate them
- **WCAG:** 1.3.1, 4.1.2
- **Recommendation:** Add `id` to inputs and `htmlFor` to labels
- **Suggested command:** `/harden`

**[P1] Clickable divs not keyboard-accessible**
- **Location:** `app/components/Sidebar.tsx:194` (prospect-item divs with onClick), `app/campaigns/page.tsx:396-401` (campaign card divs with onClick)
- **Category:** Accessibility
- **Impact:** Keyboard-only users cannot activate these elements; no focus indicator, no role="button", no keyboard handler
- **WCAG:** 2.1.1 Keyboard
- **Recommendation:** Use `<button>` elements or add `role="button"`, `tabIndex={0}`, and `onKeyDown` handlers
- **Suggested command:** `/harden`

**[P1] Massive hard-coded color usage**
- **Location:** All component files -- inline styles throughout
- **Category:** Theming
- **Impact:** Colors cannot be changed centrally. No light theme possible. Maintenance nightmare.
- **Examples:** `'#94a3b8'` (~30 occurrences), `'#e2e8f0'` (~15), `'#64748b'` (~10), `'#ef4444'`, `'#10b981'`, `'#f59e0b'`, `'#6366f1'`, `'#4ade80'`, `'#a5b4fc'`, `'#0a66c2'`, `'white'`/`'#fff'` (~20), plus CSS: `.section-title { color: #94a3b8 }`, `.stat-value { color: #fff }`
- **Recommendation:** Map all hard-coded colors to CSS custom properties. Add `--text-muted`, `--text-secondary`, `--success`, etc. tokens and use `var()` references exclusively.
- **Suggested command:** `/normalize`

**[P1] No light theme support**
- **Location:** `app/globals.css` -- only `:root` with dark values, no `@media (prefers-color-scheme: light)` or theme class
- **Category:** Theming
- **Impact:** Users who prefer light mode get a forced dark interface. The CLAUDE.md references light theme tokens that don't exist in the CSS.
- **Recommendation:** Add a `[data-theme="light"]` or `.light` class with inverted token values
- **Suggested command:** `/normalize`

**[P1] Unmemoized expensive computations in render**
- **Location:** `app/page.tsx:588-598` (`getHealthData()` called 3 times per render at lines 802, 810, 823), `app/page.tsx:607-621` (`filteredLeads` recalculated every render), `app/page.tsx:906` (`Array.from(new Set(...))` for segment options)
- **Category:** Performance
- **Impact:** Unnecessary recomputation on every render, especially costly with large lead datasets
- **Recommendation:** Wrap in `useMemo` with appropriate dependency arrays
- **Suggested command:** `/optimize`

**[P1] Excessive inline styles**
- **Location:** All component files, especially `app/page.tsx` and `app/campaigns/page.tsx`
- **Category:** Anti-Pattern / Theming
- **Impact:** Styles are not reusable, not overridable by media queries, bloat the JSX, and bypass the design system. Estimated 200+ inline style objects across the codebase.
- **Recommendation:** Extract repeated style patterns into CSS classes in `globals.css`
- **Suggested command:** `/extract` then `/normalize`

**[P1] Small touch targets on action buttons**
- **Location:** `app/page.tsx:1026-1061` (lead table action buttons: `padding: '0.4rem'`, no min-width/height), `app/page.tsx:711-716` (STOP button: `padding: '2px 8px'`), `app/campaigns/page.tsx:308-313` (Eye preview button: `padding: '0.5rem'`, no min dimensions)
- **Category:** Responsive Design / Accessibility
- **Impact:** Difficult to tap on mobile; fails WCAG 2.5.8 Target Size
- **WCAG:** 2.5.8 Target Size (Minimum)
- **Recommendation:** Ensure all interactive elements have `min-width: 44px; min-height: 44px`
- **Suggested command:** `/adapt`

---

### P2 Minor Issues

**[P2] back-link element is 40x40px**
- **Location:** `app/globals.css:588-599` -- `.back-link { width: 40px; height: 40px }`
- **Category:** Responsive / Accessibility
- **Recommendation:** Increase to 44x44px to meet `--touch-target-min`
- **Suggested command:** `/adapt`

**[P2] AIChat copy button undersized**
- **Location:** `app/components/AIChat.tsx:205-218` -- `padding: '4px'`, no min dimensions
- **Category:** Accessibility
- **Recommendation:** Add min-width/min-height of 44px or increase padding
- **Suggested command:** `/adapt`

**[P2] No debounce on window resize listeners**
- **Location:** `app/components/AIChat.tsx:102-106`, `app/components/Sidebar.tsx:62-67`
- **Category:** Performance
- **Impact:** Resize handler fires rapidly, causing many re-renders
- **Recommendation:** Debounce with 150-250ms delay
- **Suggested command:** `/optimize`

**[P2] `discoverySteps` array recreated every render**
- **Location:** `app/page.tsx:152-158` -- defined inside component body
- **Category:** Performance
- **Recommendation:** Move to module-level constant outside the component
- **Suggested command:** `/optimize`

**[P2] `transition: all 0.3s ease` animates layout properties**
- **Location:** `app/globals.css:48,80,401` -- `--transition-default: all 0.2s ease`, `.main-content { transition: all 0.3s ease }`
- **Category:** Performance
- **Impact:** Transitions all properties including width, padding, margin which trigger layout
- **Recommendation:** Be specific: `transition: background-color 0.2s ease, border-color 0.2s ease, color 0.2s ease`
- **Suggested command:** `/optimize`

**[P2] No skip-to-content link**
- **Location:** `app/layout.tsx` / all pages
- **Category:** Accessibility
- **Impact:** Keyboard users must tab through entire sidebar to reach main content
- **WCAG:** 2.4.1 Bypass Blocks
- **Recommendation:** Add a visually-hidden skip link as first focusable element
- **Suggested command:** `/harden`

**[P2] Charts lack text alternatives**
- **Location:** `app/page.tsx:800-818` (PieChart), `app/insights/page.tsx:189-208` (PieChart), `app/insights/page.tsx:217-235` (BarChart)
- **Category:** Accessibility
- **Impact:** Screen readers get no information from chart visualizations
- **WCAG:** 1.1.1 Non-text Content
- **Recommendation:** Add `aria-label` on chart containers describing the data, or provide a data table alternative
- **Suggested command:** `/harden`

**[P2] Campaign back button missing aria-label**
- **Location:** `app/campaigns/page.tsx:226` -- icon-only button with `<ArrowLeft>` but no aria-label
- **Category:** Accessibility
- **Recommendation:** Add `aria-label="Back to campaign list"`
- **Suggested command:** `/harden`

**[P2] Heavy backdrop-filter usage**
- **Location:** `app/globals.css:537,551` (`.card`, `.glass-card`), `app/globals.css:908,988` (mobile sidebar/header), `app/components/AIChat.tsx:155`
- **Category:** Performance
- **Impact:** `backdrop-filter: blur(20-40px)` is GPU-intensive, especially on mobile. Applied to cards, sidebar, mobile header, and AI chat simultaneously.
- **Recommendation:** Reduce blur radius on mobile, or use solid backgrounds for non-essential glass effects
- **Suggested command:** `/optimize`

**[P2] Duplicate sidebar backdrop in page.tsx**
- **Location:** `app/page.tsx:644-649` -- renders `sidebar-mobile-backdrop` separately, but `Sidebar.tsx:87-90` already renders its own backdrop
- **Category:** Anti-Pattern
- **Impact:** Two overlapping backdrop elements when sidebar is open
- **Recommendation:** Remove the duplicate in page.tsx; let Sidebar handle its own backdrop
- **Suggested command:** `/distill`

---

### P3 Polish Issues

**[P3] `!important` overuse in mobile CSS**
- **Location:** `app/globals.css:897-970` -- 20+ `!important` declarations in mobile breakpoint
- **Category:** Anti-Pattern
- **Impact:** Hard to override, signals specificity wars
- **Recommendation:** Refactor sidebar CSS to avoid needing `!important`; use more specific selectors or CSS layers
- **Suggested command:** `/normalize`

**[P3] Utility classes duplicate Tailwind naming**
- **Location:** `app/globals.css:1083-1135` -- `.flex`, `.gap-3`, `.text-xs`, `.bg-white\/5`, etc.
- **Category:** Anti-Pattern
- **Impact:** Confusing -- looks like Tailwind but isn't. Incomplete subset with no docs.
- **Recommendation:** Either adopt Tailwind or rename these to project-specific names
- **Suggested command:** `/normalize`

**[P3] AIChat z-index (1000) exceeds defined scale**
- **Location:** `app/components/AIChat.tsx:132,148` -- `zIndex: 1000`
- **Category:** Anti-Pattern
- **Impact:** Z-index scale in globals.css maxes at 501 (modal). Chat at 1000 breaks the system.
- **Recommendation:** Use `var(--z-chat)` (400) or define a new token
- **Suggested command:** `/normalize`

**[P3] `alert()` used for user feedback**
- **Location:** `app/page.tsx:339,529,1178,1189`, `app/page.tsx:1388`
- **Category:** Anti-Pattern
- **Impact:** Blocks the main thread, poor UX, no styling control
- **Recommendation:** Replace with toast notifications
- **Suggested command:** `/delight`

**[P3] `console.error` is the only error handling**
- **Location:** All fetch calls across all components
- **Category:** Anti-Pattern
- **Impact:** Users see no feedback when API calls fail silently
- **Recommendation:** Add user-visible error states/toasts for failed operations
- **Suggested command:** `/harden`

---

## Patterns & Systemic Issues

1. **Hard-coded colors are the norm, tokens are the exception.** Despite a well-structured `:root` token system, the vast majority of colors are inline hex values. This is the single biggest systemic issue -- it blocks theming, dark/light mode, and centralized design changes.

2. **Inline styles dominate over CSS classes.** Most components use `style={{...}}` for layout, spacing, and colors. This makes responsive overrides impossible (can't use media queries on inline styles) and prevents style reuse.

3. **Form accessibility is inconsistent.** Modals have good ARIA (role, aria-modal, aria-labelledby, ESC handlers), but form controls often lack labels. The pattern: structural a11y is good, form-level a11y needs work.

4. **Interactive elements that aren't semantic buttons.** Several clickable `div` elements exist that should be `button` elements for keyboard accessibility.

---

## Positive Findings

1. **Modal accessibility is strong** -- All modals use `role="dialog"`, `aria-modal="true"`, `aria-labelledby`, and ESC key handlers. Close buttons have `aria-label`.

2. **Focus styles are global and consistent** -- `:focus-visible` with 2px primary outline is defined for all interactive elements.

3. **Touch target awareness exists** -- `--touch-target-min: 44px` is defined and used on primary/secondary buttons and nav items.

4. **Responsive layout is well-architected** -- 5 breakpoints, mobile sidebar drawer with backdrop, mobile header, horizontal scroll for tables, `clamp()` for fluid typography.

5. **Real-time updates via Supabase subscription** -- Efficient use of `postgres_changes` channel with proper cleanup.

6. **useCallback/useMemo used for data fetching** -- Fetch functions are properly memoized to prevent infinite loops in useEffect.

7. **Proper TypeScript interfaces** -- All data types have explicit interfaces, no `any` types found.

8. **CSS custom properties architecture is sound** -- The token system in `:root` is well-organized (colors, spacing, z-index, transitions). The problem is adoption, not design.

---

## Recommended Actions

1. **[P1] `/normalize`** -- Map all 50+ hard-coded hex colors to CSS custom properties. This is the highest-impact single change.
2. **[P1] `/harden`** -- Fix form labels, keyboard accessibility for clickable divs, chart alternatives, and missing aria-labels.
3. **[P1] `/adapt`** -- Fix undersized touch targets on table action buttons, STOP button, preview button, copy button, and back-link.
4. **[P1] `/optimize`** -- Memoize `getHealthData`, `filteredLeads`, segment options. Debounce resize listeners. Move constants out of component. Narrow transition properties.
5. **[P1] `/extract`** -- Extract repeated inline style patterns into reusable CSS classes.
6. **[P2] `/distill`** -- Remove duplicate sidebar backdrop, clean up unused utility classes.
7. **[P3] `/delight`** -- Replace `alert()` calls with toast notifications.
8. **[P3] `/polish`** -- Final pass for z-index consistency, `!important` cleanup, error state UX.

> You can ask me to run these one at a time, all at once, or in any order you prefer.
>
> Re-run `/audit` after fixes to see your score improve.
