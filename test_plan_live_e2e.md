# End-to-end test plan — live `https://vantavcommerce.com` (`devin/profit-feed`)

## Scope
Prove the latest deployed build on Render works for the `test_rules_engine@example.com` merchant by exercising login, the new overview cards, the forecast/cron alert pipeline, action approve/verify, and channel analytics.

## Preconditions
- Browser maximized.
- Live site: `https://vantavcommerce.com`.
- Test credentials: `test_rules_engine@example.com` / `TestPass123!`.
- One existing SKU-level `inventory_runout` alert is resolved before the recording so `/api/v1/forecast/cron` can create a fresh alert/action during the test.

## Test — Live end-to-end recorded flow

### 1. Login and dashboard overview cards
**Actions**
1. Navigate to `https://vantavcommerce.com/login`.
2. Enter `test_rules_engine@example.com` / `TestPass123!` and submit.
3. Wait for `/dashboard` to load.
4. Scroll to the `AI Actions` card and `Channel Performance` card.

**Pass criteria**
- Page heading reads `Good morning, Rules Test.`
- `AI Actions` card is visible with a hero action title (e.g., `Reorder SZL-VAR-A before stockout`) and an `Approve & Execute` button.
- `Channel Performance` table is visible and shows at least one row (e.g., `shopify`) with `Net Profit` and `Margin` columns.
- `True Profit` KPI shows `$236` and `Revenue` `$914`.

### 2. Channel analytics API returns true profit per channel
**Actions**
1. Open a new tab.
2. Navigate to `https://vantavcommerce.com/api/analytics/channels?days=30`.

**Pass criteria**
- HTTP 200 JSON response.
- `channels` array contains `shopify`, `tiktok`, `amazon`, `etsy`.
- Each channel object has `revenue`, `net_profit`, `margin_pct`, `orders`.
- `totals.net_profit` equals the sum of channel `net_profit`.

### 3. POST `/api/v1/forecast/cron` creates a SKU-level reorder alert
**Actions**
1. Resolve an existing `inventory_runout` alert for `SZL-VAR-A` before the test (setup, not recorded).
2. In the browser, call `POST /api/v1/forecast/cron` and display the response JSON on the page (or navigate to `/api/actions` before/after).
3. Open `https://vantavcommerce.com/api/actions`.

**Pass criteria**
- `forecast/cron` returns HTTP 200 and a `reports` array with SKU-level forecasts (`sku`, `predicted_daily_velocity`, `suggested_reorder_qty`).
- After the POST, `/api/actions` pending list contains a new `reorder` action for `SZL-VAR-A` (or a matching SKU) with `status` `pending`.

### 4. Action Gate lists pending actions with evidence and allows approve/deny
**Actions**
1. Navigate to `https://vantavcommerce.com/dashboard/action-gate`.
2. Locate a pending `reorder` action.
3. Trigger `approveAction(id)` (browser automation) or click the `Approve` button.
4. Wait for page reload.

**Pass criteria**
- `Action Gate` page shows `Pending Approvals` with at least one action.
- The action displays evidence fields (e.g., `Confidence`, `Expected impact`, `Market`).
- After approval, the action moves from `Pending Approvals` to `Recent Decisions` with status `approved`/`executed`.
- `POST /api/actions/<id>/approve` returns HTTP 200 with `status: "approved"` and a `message` (e.g., created PO reference).

### 5. POST `/api/actions/<id>/verify` returns before/after metrics
**Actions**
1. Note the approved action ID from step 4.
2. Call `POST /api/actions/<id>/verify` and display the JSON response on the page.
3. Navigate back to `/dashboard/action-gate`.

**Pass criteria**
- `verify` returns HTTP 200 with `status: "verified"`.
- Response contains `before_metrics` and `after_metrics` objects with `net_profit`, `gross_revenue`, `orders`, etc.
- `report` string contains `Verified after execution` and before/after net profit values.
- Action Gate history table shows `Verified: ...` under the approved action.

## Notes
- The `left_click` action is unreliable in this managed Chrome environment; use `Return` for form submission and `browser_console` calls for approve/verify/forecast triggers.
- reCAPTCHA v3 is optional in the current beta login flow; an empty token still allows login.
