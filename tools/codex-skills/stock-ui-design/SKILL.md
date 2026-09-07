---
name: stock-ui-design
description: Design, refactor, and visually validate the stock project's dashboards, K-line and minute charts, trading trainers, grid simulator, journal, and news pages. Use for UI, CSS, ECharts, responsive layout, accessibility, or browser visual QA work in this repository; do not use for backend-only changes.
---

# Stock UI Design

Build a coherent professional trading workstation: compact, legible, information-dense, and calm. Preserve trading semantics and simulation correctness while improving appearance.

## Sources of Truth

- Reuse `scripts/services/static/app.css` for shared tokens and primitives.
- Reuse `scripts/services/static/app-shell.js` for navigation, theme, and density behavior.
- Inspect the actual served page before changing it. Do not design from a template file alone.
- Move genuinely shared improvements into the shared stylesheet; keep page-specific chart and layout rules near the page.

## Workflow

1. Open the relevant local route and capture its current desktop and mobile states.
2. Identify the primary decision the page supports and order information around that decision.
3. Define or reuse semantic tokens before adding literal colors, spacing, radii, or shadows.
4. Implement the smallest coherent component changes, preserving existing API contracts and trading logic.
5. Validate the important interactive states in a real browser and compare screenshots.

Use Figma for broad structural redesigns or design-system work. Use Playwright for repeatable navigation, interaction, responsive screenshots, and regression checks. Use Chrome DevTools when computed CSS, runtime errors, network behavior, or rendering performance needs diagnosis.

## Visual System

- Keep the existing A-share convention: red means price up or buy-side market movement; green means price down. Do not use those colors for generic success and failure when that creates ambiguity.
- Use the accent blue for navigation, selection, links, and neutral primary actions. Give buy, sell, warning, pending, filled, cancelled, and failed states both text and shape cues, not color alone.
- Follow the shared 4/8/12/16/24 spacing rhythm. Prefer borders and subtle surface changes over decorative gradients or heavy shadows.
- Render prices, percentages, quantities, times, and money with tabular numerals. Right-align comparable numeric columns.
- Make labels quieter and smaller than values. Separate `label`, `value`, `unit`, and explanatory text by size, weight, color, and spacing.
- Maintain compact and comfortable density modes. Avoid oversized cards that reduce how much market context is visible.
- Use one clear page title, one primary action per region, and progressive disclosure for advanced settings.

## Trading Information Hierarchy

- Separate market state, account state, planned action, pending order, execution result, and review notes into distinct regions.
- Keep the current quote and its change relative to previous close together.
- Expose T+1 locked quantity, available quantity, trigger stage, and cancellation state near the action they constrain.
- For condition orders, show the reference value, activation threshold, tracking extreme, secondary trigger, order price rule, validity, and current stage.
- Empty, loading, unavailable, waiting, and error states must explain what the user can do next.

## Charts

- Keep price and change-percentage axes aligned around the previous-close reference line.
- Distinguish current price, previous close, average price or VWAP, grid levels, and trade markers consistently.
- Tooltips must show time, OHLC or minute price, change percentage, volume, amount, and relevant annotations without hiding the inspected point.
- Replay charts keep the full trading-session x-axis fixed while the curve reveals from left to right.
- Never reveal future candles, future minute points, future news, or future annotations in no-lookahead training modes.
- Use restrained animation and honor `prefers-reduced-motion`.

## Responsive and Modal Rules

- Validate at approximately 1440x900, 1024x768, and 390x844.
- Keep the main quote and primary action visible without horizontal scrolling.
- Collapse secondary panels deliberately; do not merely shrink all typography.
- Bottom sheets and dialogs must fit within `100dvh`, keep their header and submit/cancel actions reachable, and scroll only their content region.
- Provide touch targets near 40px or larger for frequently used trading controls.

## Browser Acceptance

Check at least:

- No clipped text, overlapping cards, off-screen dialogs, accidental horizontal scroll, or unreadable label/value groups.
- Hover, focus, keyboard, touch, loading, empty, disabled, pending, triggered, filled, cancelled, and failure states.
- Theme and density switches, chart resize, dynamic replay, condition-order creation and cancellation, and back/forward navigation.
- Console errors and failed network requests.

When a visual baseline exists, update it only after confirming the change is intentional.
