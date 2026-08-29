# Vantav live public-testing adversarial full-site audit

**Scope:** Runtime end-to-end test of `https://vantavcommerce.com` covering admin, engineer, and merchant roles, tier chooser and gating, member/feature/billing management, support chat, audit log, platform controls, store sync/reset UI, regression chart, public health endpoints, and security/UX regressions.

**Forbidden:** Do not run Stripe checkout or the live micro-charge test. Do not actually unlink production stores or broadcast real announcements. Prefer non-destructive verification of sync/reset controls.

**Accounts**
- Admin: `shawn@shawnzyluxe.com` / `VantavMaster2025!`
- Engineer: `engineer@shawnzyluxe.com` / `EngVantav2025!`
- Tier-test merchant: `merchant@vantavcommerce.com` / `MerchantTest2025!`

**Environment**
- Live URL: `https://vantavcommerce.com`.
- Session cookie: `vantav_session_token`.
- `auth_login` resets the tier-test merchant to `Basic Tier`/`pending`/`live_access=0` on every login, so the merchant always lands on `/choose-tier`.
- Global switch `sample_pages_enabled` must remain **off** for this audit per the user's instruction to hide all non-beta-ready pages before public testing. The tier-test account (`merchant@vantavcommerce.com`) bypasses the beta lock and is used to verify tier/page gating.

---

## Setup

1. Verify public health endpoints with `curl` (no recording):
   - `GET https://vantavcommerce.com/health` → 200, `status: HEALTHY`, `database_connected: true`, `generated_storage_write_access: true`.
   - `GET https://vantavcommerce.com/api/v1/health` → 200, same JSON.
   - `GET https://vantavcommerce.com/status` → 302 to `/login` while site wall is enabled.
2. Launch three separate Chrome incognito profiles (admin, engineer, merchant) using the Chrome for Testing binary with `--user-data-dir`.
3. Maximize the browser window.
4. Start a single screen recording covering the main audit.
5. `annotate_recording` `setup` `description="Verified live health and launched admin/engineer/merchant incognito sessions"`.

---

## Test 1 — Site wall, login, and role routing

1. Open `https://vantavcommerce.com/login` in a fresh Chrome profile.
2. Log in as **admin** `shawn@shawnzyluxe.com` / `VantavMaster2025!`.
3. Expected: response `{status: "AUTHORIZED", role: "Admin"}`, redirect to `/admin`, page title `Admin Dashboard`.
4. Verify admin routes render:
   - `/admin` → 200.
   - `/admin/merchants` → 200.
   - `/admin/chat` → 200.
   - `/admin/audit` → 200.
5. Log in as **engineer** `engineer@shawnzyluxe.com` / `EngVantav2025!` in a second profile.
6. Expected: response `{status: "AUTHORIZED", role: "Engineer"}`, redirect to `/engineer`.
7. Verify engineer routes:
   - `/engineer` → 200.
   - `/admin/merchants` → 200, but **no Edit button** in members table.
   - `/admin/chat` → 200.
   - `/admin/audit` → 200.
8. Log in as **merchant** `merchant@vantavcommerce.com` / `MerchantTest2025!` in a third profile.
9. Expected: response `{status: "AUTHORIZED", role: "Merchant"}`, immediate redirect to `/choose-tier` because `_reset_test_merchant_for_tier_testing` sets `sandbox_status=pending`.

**Pass criteria:** all three credentials authorize with correct roles, admin/engineer reach their dashboards, merchant lands on tier chooser.

---

## Test 2 — Admin platform controls, members, billing override, feature flags

1. In the admin session, load `/admin`.
2. Locate the **Platform controls** card. Check the `Show non-beta modules to merchants` checkbox, click **Save platform controls**, and verify the status text changes to `Saved.` and the checkbox stays checked.
3. Refresh `/admin` and confirm `loadPlatformControls()` still shows `sample_pages_enabled` checked.
4. Navigate to `/admin/merchants`.
5. Locate the tier-test merchant `tenant_4732bbf6` (`merchant@vantavcommerce.com`).
6. Click **Edit** (or run `javascript:editMember('tenant_4732bbf6')`).
7. In the Edit modal:
   - Change **Tier** to `Vantav Growth`.
   - Set **Sandbox status** to `approved`.
   - Check **Live access enabled**.
   - Set **Current plan** override to `Vantav Growth`.
   - Set **Max seats** to `3`.
   - Toggle the `products` feature flag to **On**.
8. Click **Save changes**. The row should update to reflect `Vantav Growth`, `Approved`, `LIVE`, and `1 on / 0 off`.
9. In the merchant session, refresh `/dashboard`. The sidebar should now show Growth-tier pages, including **Catalog → Products**.
10. Directly visit `/dashboard/products` in the merchant session. It should render (not redirect) because `products` is allowed for Growth and `sample_pages_enabled` is on.
11. Return to `/admin/merchants`, open Edit for the same merchant, set tier back to `Basic Tier`, sandbox `pending`, uncheck live access, clear current plan, clear max seats, and set `products` to **Default** (or Off). Save and confirm the row returns to `Basic Tier / Pending` and `0 on / 0 off`.

**Pass criteria:** admin can toggle global platform controls, edit member tier/sandbox/live access/billing overrides/feature flags, and the merchant sidebar/direct access reflects those changes without a 500.

---

## Test 3 — Admin support chat, audit log, and store sync/reset UI

1. From the merchant session, click the floating support widget and send `Audit test message from merchant`.
2. In the admin session, open `/admin/chat`.
3. Verify the merchant `tenant_4732bbf6` thread appears with an unread badge.
4. Open the thread (`loadThread('tenant_4732bbf6')`) and reply `Admin audit reply`.
5. In the merchant session, wait for the 8-second poll or refresh the widget and confirm the admin reply appears.
6. In the admin session, open `/admin/audit`. Verify the log contains `ADMIN.LOGIN`, `platform_control.set`, `MERCHANT.UPDATE`, and `TIER_SELECTED` events.
7. On `/admin`, locate the **Connected stores** table. If no stores are connected, confirm the table shows `No connected stores.` and the `Unlink` button is not present. If a store is connected, note it but **do not click Unlink**.
8. On `/admin/merchants`, locate the `Sync S`, `Sync T`, `Reset S` buttons for `tenant_4732bbf6`. Click `Sync S` (or call `syncChannel('tenant_4732bbf6', 'shopify')`) and verify the action returns either a success toast or a graceful error (not a 500). Do **not** perform destructive `Unlink`.

**Pass criteria:** two-way support chat works, audit log records admin/platform/merchant actions, store sync/reset UI is reachable and returns a non-500 response.

---

## Test 4 — Engineer panel and role restrictions

1. In the engineer session, load `/engineer`.
2. Verify the page fetches and renders:
   - System status `HEALTHY`.
   - Request Count, P95 Latency, Error Rate, Exceptions (24h), Connected Stores.
3. In the Engineer Assistant chat, type `health` and click Send. A JSON response should appear in the chat history.
4. Type `metrics` and Send; a metrics JSON response should appear.
5. Type `exceptions` and Send; an exceptions JSON response should appear.
6. Navigate to `/admin/merchants` in the engineer session. Verify the page loads but the **Edit** button is not rendered (`is_admin` is false).
7. Attempt to call an admin-only mutation from the address bar:
   - `fetch('/api/admin/stores/tenant_4732bbf6/shopify/unlink', {method:'POST', credentials:'same-origin'})`
   - Expected response status `403`.
8. Attempt to call `PATCH /api/admin/merchants/tenant_4732bbf6` from the engineer session. Expected `403`.

**Pass criteria:** engineer sees health/metrics/exceptions, chatbot works, member list is read-only, admin-only mutations return 403.

---

## Test 5 — Tier chooser and tier/page gating (with `sample_pages_enabled` off)

With `sample_pages_enabled` off, non-beta-locked pages are hidden from regular merchants, but tier gating and the tier-test account bypass still apply.

### 5.1 Basic Tier
1. In the merchant session, if not already on `/choose-tier`, log in again.
2. Select **Basic Tier**.
3. Expected `POST /api/merchant/select-tier` returns 200 `{status: "ok", tier: "Basic Tier", redirect: "/dashboard"}`.
4. On `/dashboard`, the sidebar should show only: Overview, Alerts, Profit Dashboard (or Profit Engine), Action Gate, Billing, Settings, Support.
5. Direct access:
   - `/dashboard/overview` → 200.
   - `/dashboard/alerts` → 200.
   - `/dashboard/profit-engine` → 200.
   - `/dashboard/settings` → 200.
   - `/dashboard/billing` → 302 to `/dashboard/settings?tab=billing`.
   - `/dashboard/inventory` → upgrade banner requiring **Vantav Operator**.
   - `/dashboard/not-a-real-page` → 302 to `/dashboard`.

### 5.2 Vantav Operator
1. Return to `/choose-tier` (log out and log in again, or use admin to set sandbox pending).
2. Select **Vantav Operator**.
3. Sidebar should add: Inventory, Orders, Products, Store Catalog, Commerce Hub, Customers.
4. Direct access:
   - `/dashboard/inventory` → 200.
   - `/dashboard/products` → 200.
   - `/dashboard/predictions` → upgrade banner requiring **Vantav Growth**.

### 5.3 Vantav Growth
1. Select **Vantav Growth**.
2. Sidebar should add Growth pages: Command Center, Monitoring, Predictions, Health Score, Marketing, Automations, Team AI, Product Research, Fulfillment, Returns, Shipments, Suppliers, TikTok Studio, Discounts, Analytics, Mobile.
3. Direct access:
   - `/dashboard/predictions` → 200.
   - `/dashboard/analytics` → 200.
   - `/dashboard/fraud` → upgrade banner requiring **Vantav Scale**.

### 5.4 Vantav Scale
1. Select **Vantav Scale**.
2. Sidebar should add Scale pages: Fraud, Startup Pack, Apps, Reports, Regression Chart.
3. Direct access:
   - `/dashboard/fraud` → 200.
   - `/dashboard/regression_chart` or `/dashboard/regression-chart` → 200.
   - `/dashboard/startup-pack` → 302 to `/dashboard/settings?tab=billing` (unless concierge bundle is attached).

### 5.5 Regression chart
1. With the merchant on **Vantav Scale**, visit `/dashboard/regression_chart?sku=SKU-404-PODS`.
2. The page should **not** show `Unable to load regression data`.
3. The canvas should display a Chart.js line chart with two datasets (white scatter points and a dashed gold OLS trend line) and diagnostics showing `Trend`, `R² Confidence`, and `7-Day Projection`.

**Pass criteria:** every tier selection succeeds, sidebar pages match the tier, direct access to locked pages shows an upgrade banner or redirect (never 500), regression chart renders with a line chart.

---

## Test 6 — Public health, monitoring, GDPR webhooks, and 404 handling

Run with `curl` (no recording):

1. Public health:
   - `GET /health` → 200 `HEALTHY`.
   - `GET /api/v1/health` → 200 `HEALTHY`.
2. Role-gated monitoring (admin/engineer cookie):
   - `GET /api/v1/monitoring/health` → 200.
   - `GET /api/v1/monitoring/metrics` → 200.
   - `GET /api/engineer/exceptions` → 200 for engineer, 403 for admin.
3. GDPR/webhook endpoints should not 500 when hit without valid signatures; expected 400/401/rejected:
   - `POST /api/v1/webhooks/shopify-orders` → 401 `Missing HMAC`.
   - `POST /api/v1/webhooks/tiktok-orders` → non-500 (200 `ignored` or 400).
   - `POST /api/v1/webhooks/amazon-orders` → non-500.
   - `POST /api/v1/webhooks/shopify-gdpr/*` → 401 `Missing HMAC`.
   - `POST /api/v1/webhooks/shopify/app/uninstalled` → 401.
   - `POST /api/v1/webhooks/stripe-billing` → 400/401.
4. Invalid admin route `/admin/not-real` → 404 (preferably styled; plain Flask 404 is acceptable but worth noting).

**Pass criteria:** health endpoints healthy, monitoring endpoints work for authorized roles, webhook endpoints reject unsigned traffic without crashing, unknown routes handled.

---

## Test 7 — Console errors, broken nav, stale forms, placeholder shells, and security

1. Open the browser console on `/dashboard`, `/admin`, `/engineer`, `/choose-tier`, and `/dashboard/regression_chart`.
2. Watch for JavaScript errors, especially:
   - `TypeError: Failed to fetch` in `loadMessages` (merchant support widget poll).
   - `TypeError: (hero.evidence || []).forEach is not a function` on dashboard pages.
   - Any CSP errors blocking `cdn.jsdelivr.net` on the regression chart.
3. Check for stale form state:
   - After toggling `sample_pages_enabled` or editing a member, refresh the page and confirm the UI reflects the persisted value.
   - After the merchant selects a tier in one tab, the admin members table should reflect the new tier on refresh (not the old cached value).
4. Check for placeholder shells:
   - Pages in `PLACEHOLDER_DASHBOARD_PAGE_IDS` should display the placeholder banner from `dashboard/base.html` rather than a blank page when `sample_pages_enabled` is on.
   - Pages not in the merchant’s tier should show an upgrade banner, not a blank page.
5. Security checks:
   - No API returns the plaintext `SITE_WALL_PASSWORD`, admin password hashes, or session tokens in JSON responses.
   - The `/api/v1/health` endpoint does not leak internal keys.
   - Engineer cannot access admin-only mutations.

**Pass criteria:** no unexpected console errors, no stale form state, no blank placeholder/locked pages, no exposed secrets.

---

## Cleanup

1. In the admin session, set the tier-test merchant back to `Basic Tier`, `sandbox_status=pending`, `live_access_enabled=false`, and clear billing overrides and feature flags.
2. Leave `sample_pages_enabled` turned **off** to match the user's public-testing gate.
3. Stop the recording.

---

## Evidence to capture

- Continuous screen recording of the full audit.
- Screenshots:
  - Admin dashboard with `sample_pages_enabled` checked and `Saved.` status.
  - `/admin/merchants` Edit modal showing tier/sandbox/live/billing/feature-flag changes.
  - Merchant sidebar for each tier.
  - Locked page upgrade banner.
  - `/dashboard/regression_chart?sku=SKU-404-PODS` with a rendered Chart.js line chart and diagnostics.
  - `/admin/chat` thread with unread badge and admin reply.
  - `/admin/audit` showing relevant events.
  - Engineer dashboard with health/metrics/exceptions and chatbot responses.
  - Engineer `/admin/merchants` showing no Edit button.
  - Public health and webhook `curl` outputs.
  - Browser console error logs (if any).
- `annotate_recording` markers at each test start and each significant assertion.
