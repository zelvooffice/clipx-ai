## Accessibility Audit: CLIPX AI dashboard (all 8 pages)
**Standard:** WCAG 2.1 AA | **Date:** 2026-11-11 | **Method:** axe-core 4 (wcag2a, wcag2aa, wcag21aa) on every page
via Playwright + keyboard walk-through + computed contrast ratios. Screen-reader testing with VoiceOver/NVDA is
still recommended before launch (not possible in the build container).

### Summary
**Initial issues:** 4 | **Critical:** 0 | **Major:** 2 | **Minor:** 2 — **all fixed; final axe run: 0 violations.**

### Findings (all resolved)
| # | Issue | WCAG | Severity | Fix |
|---|---|---|---|---|
| 1 | Empty-state `<div>` rendered directly inside `<ul>` (Overview, Live Monitor) | 1.3.1 | 🟡 Major | Wrapped in `<li>` |
| 2 | White text on violet button gradient start (#8b5cf6) = 4.23:1 | 1.4.3 | 🟡 Major | Gradient stops deepened to #7c3aed (5.7:1) and #2563eb (5.17:1) |
| 3 | `<button>` nested inside `<a>` ("Open settings", "Connect TikTok", "All clips") — double tab stop | 4.1.2 | 🟢 Minor | Links styled as buttons (`linkButton`) |
| 4 | No skip link to bypass the 8-item navigation | 2.4.1 | 🟢 Minor | "Skip to content" link + `main#main` target |

### Color Contrast Check (dark theme, computed)
| Element | Foreground | Background | Ratio | Required | Pass? |
|---|---|---|---|---|---|
| Body text | #f4f4f5 | #111113 | 17.16:1 | 4.5:1 | ✅ |
| Secondary text | #c4c4cc | #111113 | 10.88:1 | 4.5:1 | ✅ |
| Muted text / axis labels | #8d8d97 | #111113 | 5.74:1 | 4.5:1 | ✅ |
| Muted text on raised card | #8d8d97 | #17171a | 5.44:1 | 4.5:1 | ✅ |
| Status badges (good / warn / critical) | #5fd35f / #fbc94a / #f08a8a | #111113 | 9.85 / 12.17 / 7.82:1 | 4.5:1 | ✅ |
| Primary button text | #ffffff | #7c3aed → #2563eb | 5.70 → 5.17:1 | 4.5:1 | ✅ |
| Chart marks (series 1–3) | #3987e5 / #d95926 / #199e70 | #111113 | 5.18 / 4.86 / 5.54:1 | 3:1 | ✅ |
| Focus ring | #a78bfa | #111113 | 6.93:1 | 3:1 | ✅ |

### Keyboard Navigation
| Element | Tab order | Enter/Space | Escape | Notes |
|---|---|---|---|---|
| Skip link → logo → 8 nav links → page actions | logical, top-to-bottom | activates | — | visible 2px outline on every stop |
| Toggles (`role="switch"`) | in flow | toggles | — | `aria-checked`, accessible name |
| Tabs (`role="tablist"`) | in flow | selects | — | `aria-selected` |
| Dialogs (queue, confirm, viral, consent) | trapped in visual order | — | close button | `role="dialog"`, `aria-modal`, labelled |
| Charts | "Table" toggle | switches to data table | — | every chart has an equivalent table |

### Design-level guarantees
- Status is never colour-only: every badge carries an icon + text; timeline marks also differ by **shape**.
- Chart palette validated for colour-vision deficiency (dataviz validator: CVD ΔE ≥ 9.4, normal ΔE ≥ 20.9).
- `prefers-reduced-motion` disables animations; video never autoplays with sound (hover previews are muted).

### Remaining recommendations
1. Run VoiceOver/NVDA passes on the Publishing confirmation dialog (most complex form).
2. Consider 44×44 touch targets for icon-only buttons on mobile (WCAG 2.5.5 is AAA; current 32–36px meets 2.5.8 AA in WCAG 2.2).
