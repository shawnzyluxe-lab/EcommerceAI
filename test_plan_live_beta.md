# End-to-end test plan — live Vantav beta merchant experience

## Scope
Validate the live `https://vantavcommerce.com` merchant view for the `test_rules_engine@example.com` beta account. Focus on sidebar gating, redirect behavior, the regression chart, the AI greeting banner, and responsive layout.

## Preconditions
- Browser maximized.
- Live site `https://vantavcommerce.com`.
- Merchant credentials: `test_rules_engine@example.com` / `TestPass123!`.
- `SKU-404-PODS` regression data already seeded for the merchant (verified earlier via API returning `DEGRADED`).

## Test — Live merchant beta flow

### 1. Login and dashboard landing
**Actions**
1. Navigate to `https://vantavcommerce.com/login`.
2. Enter credentials and submit.

**Pass criteria**
- Login returns 200/redirects and lands on `/dashboard`.
- Page title contains `Overview`.
- AI greeting banner is visible with personalized text (e.g., `Hi Rules Test, revenue is...`).

### 2. Merchant sidebar only shows beta-ready pages
**Actions**
1. Examine the left sidebar navigation.

**Pass criteria**
- Sidebar shows **Workspace > Overview**, **Intelligence > Alerts, Profit Dashboard, Regression**, **Store > Billing, Settings**.
- Sidebar does **not** show Command Center, Orders, Customers, Commerce Hub, Action Gate, Predictions, Product Research, Fulfillment, Fraud, Suppliers, Marketing, Support, Automations, Team, Health Score, Mobile Copilot, Inventory, Shipments, Returns, Analytics, Discounts, Apps, Themes, Reports, Integrations.

### 3. Non-beta page redirects merchant to /dashboard
**Actions**
1. Type `/dashboard/commerce-hub` in the address bar and navigate.

**Pass criteria**
- Response is HTTP 302 redirect to `/dashboard` (or the browser lands back on `/dashboard`).
- The page does not show the Commerce Hub content.

### 4. Regression chart renders for SKU-404-PODS
**Actions**
1. Navigate to `https://vantavcommerce.com/regression-chart?sku=SKU-404-PODS`.

**Pass criteria**
- Page loads with title `Vantav — Historical Margin Stability Forecast`.
- The **TREND** badge reads `DEGRADED`.
- The **R² RATING** is a percentage close to `99.01%`.
- The chart canvas is visible with a line/data points.
- The diagnostics ledger text shows the slope, R², and 7-day projected net profit (`137.23`).

### 5. AI greeting banner appears after login
**Actions**
1. After login on `/dashboard`, locate the AI greeting banner near the top.

**Pass criteria**
- A banner with `Hi Rules Test, revenue is...` or similar is visible (may require toggling/clicking or may be auto-displayed by the page).

### 6. Responsive narrow viewport
**Actions**
1. Resize the browser to a narrow mobile width (≈420 px) using `xdotool` or by dragging the window border.
2. Observe the dashboard.

**Pass criteria**
- The sidebar collapses or is hidden.
- A hamburger/menu button is visible.
- The main content reflows into a single column without horizontal scroll.

## Notes
- `left_click` is unreliable; use `Ctrl+L` address-bar navigation and `Return` for form submission.
- If `Chart.js` is blocked by CSP (`cdn.jsdelivr.net` not in `script-src`), the chart canvas may not render; report as a failure with evidence.
