# Vantav live public-testing adversarial full-site audit

**Scope:** Runtime end-to-end test of `https://vantavcommerce.com` covering PR #15 fixes (Redis cleanup, `channels_module.list_channels` store counts, `api_admin_impersonate` GET, `require_roles` messages, styled admin 404, `action_gate` in Basic tier), `sample_pages_enabled` off, admin/engineer/merchant role flows, tier chooser and gating, beta-lock for non-tier-test merchants, member/feature/billing management, support chat, audit log, platform controls, store sync/reset UI, regression chart, public health/webhook endpoints, and security/UX regressions.

**Forbidden:** Do not run Stripe checkout or the live micro-charge test. Do not actually unlink production stores or broadcast real announcements. Prefer non-destructive verification of sync/reset controls.

**Accounts**
- Admin: `shawn@shawnzyluxe.com` / `VantavMaster2025!`
- Engineer: `engineer@shawnzyluxe.com` / `EngVantav2025!`
- Tier-test merchant: `merchant@vantavcommerce.com` / `MerchantTest2025!`
- Temporary non-tier-test merchant: created via `POST /api/v1/tenant/register` and deleted before cleanup.

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

## Test 1 — Site wall, login, role routing, and `require_roles` messages

1. Open `https://vantavcommerce.com/login` in a fresh Chrome profile.
2. Log in as **admin** `shawn@shawnzyluxe.com` / `VantavMaster2025!`.
3. Expected: response `{status: "AUTHORIZED", role: "Admin"}`, redirect to `/admin`.
4. Verify admin routes render: `/admin`, `/admin/merchants`, `/admin/chat`, `/admin/audit` → 200.
5. Log in as **engineer** `engineer@shawnzyluxe.com` / `EngVantav2025!` in a second profile; expected redirect to `/engineer`.
6. Log in as **merchant** `merchant@vantavcommerce.com` / `MerchantTest2025!` in a third profile; expected redirect to `/choose-tier` because `_reset_test_merchant_for_tier_testing` sets `sandbox_status=pending`.
7. `require_roles` message checks (with `curl`, no recording):
   - `GET /api/v1/monitoring/health` without a cookie → 403 JSON `error: "Session expired or missing. Please log in."`.
   - `GET /api/v1/monitoring/health` with a logged-in **merchant** session → 403 JSON `error: "Access denied. Your session does not have the required role for this endpoint."`.

**Pass criteria:** all three credentials authorize with correct roles; missing-session and wrong-role errors are distinct.

---

## Test 2 — `sample_pages_enabled` stays off and platform controls

1. In the admin session, load `/admin`.
2. Locate the **Platform controls** card. Confirm the `Show non-beta modules to merchants` checkbox is **unchecked**.
3. Call `GET /api/admin/platform-controls` and verify `sample_pages_enabled: false`, `maintenance_mode: false`, `global_sync_paused: false`.
4. Do **not** save `sample_pages_enabled` as `true`; leave it off for the audit.

**Pass criteria:** non-beta pages remain hidden for regular merchants; platform controls report the off state.

---

## Test 3 — Admin members, channel counts, billing override, feature flags

1. Navigate to `/admin/merchants`.
2. For the tier-test merchant `tenant_4732bbf6`, verify the **Channels** column matches `/api/admin/stores` and `/dashboard/settings?tab=billing` connected-store counts (PR #15 fix for `channels_module.list_channels`).
3. Open **Edit** for `tenant_4732bbf6`.
4. Set Tier to `Vantav Growth`, Sandbox `approved`, Live access enabled, Current plan `Vantav Growth`, Max seats `3`, and toggle `products` **On**.
5. Save; the row should update to `Vantav Growth`, `Approved`, `LIVE`, `1 on / 0 off`.
6. In the merchant session, refresh `/dashboard`; the sidebar should show Growth-tier pages including **Catalog → Products**.
7. Directly visit `/dashboard/products` and confirm it renders (tier allows it; tier-test bypasses beta lock).
8. Return to `/admin/merchants`, edit the same merchant, set Tier back to `Basic Tier`, Sandbox `pending`, uncheck Live access, clear Current plan, clear Max seats, and set `products` to **Default**. Save and confirm the row returns to `Basic Tier / Pending`, `0 on / 0 off`.

**Pass criteria:** admin edits persist, channel counts are consistent, feature flags take effect, and the merchant view reflects tier/feature changes.

---

## Test 4 — Admin impersonate and styled 404

1. In the admin session, go to `/admin/merchants`.
2. Click the **Impersonate** link for `tenant_4732bbf6` (or visit `/api/admin/impersonate/tenant_4732bbf6`).
3. Expected: `GET` request redirects to `/dashboard` and loads the merchant dashboard with an impersonation banner showing the admin is viewing as the merchant.
4. Click the banner's **Stop impersonating** link (or call `/api/admin/stop-impersonating`) and confirm you return to `/admin`.
5. Visit `/admin/not-real`.
6. Expected: styled 404 page rendered by `/admin/<path:path>` catch-all (`dashboard/page.html`) with `Page not found` and a **Return to Admin Home** button, not a plain Flask 404.

**Pass criteria:** impersonation GET works and returns to admin dashboard; unknown admin paths return the styled 404.

---

## Test 5 — Admin support chat, audit log, store sync/reset UI

1. From the merchant session, open the floating support widget and send `Audit test message from merchant`.
2. In the admin session, open `/admin/chat`.
3. Verify the merchant `tenant_4732bbf6` thread appears with an unread badge.
4. Open the thread (`loadThread('tenant_4732bbf6')`) and reply `Admin audit reply`.
5. In the merchant session, wait for the 8-second poll or refresh the widget and confirm the admin reply appears.
6. In the admin session, open `/admin/audit`. Verify the log contains `ADMIN.LOGIN`, `impersonation.start`, `impersonation.stop`, `merchant.update`, and `TIER_SELECTED` events.
7. On `/admin/merchants`, for `tenant_4732bbf6` call `Sync S` (`/api/admin/shopify/sync/tenant_4732bbf6`) and verify the action returns either a success toast or a graceful `400`/`404` (e.g. "Shopify is not connected for this merchant"), never a `500`.

**Pass criteria:** two-way support chat works, audit log records admin/platform/merchant/impersonation actions, store sync returns a non-500 response.

---

## Test 6 — Engineer panel and role restrictions

1. In the engineer session, load `/engineer`.
2. Verify the page fetches and renders:
   - System status `HEALTHY`.
   - Request Count, P95 Latency, Error Rate, Exceptions (24h), Connected Stores.
3. In the Engineer Assistant chat, send: `health`, `metrics`, `exceptions`, `summary`, `run migrations`.
   - Each should produce a JSON response in the chat history.
4. Call `GET /api/engineer/exceptions`.
   - Expected: `200` JSON with `exceptions` array. After the PR #15 Redis cleanup, there should be no `TEARDOWN` entries containing `6379` or `redis` older than one hour.
5. Navigate to `/admin/merchants` in the engineer session. Verify the page loads but the **Edit** button is not rendered (`is_admin` is false).
6. Attempt `PATCH /api/admin/merchants/tenant_4732bbf6` and `POST /api/admin/stores/tenant_4732bbf6/shopify/unlink` from the engineer session. Expected `403`.

**Pass criteria:** engineer sees health/metrics/exceptions, chatbot handles the listed commands, exceptions list is clean of stale Redis TEARDOWN errors, member list is read-only, admin-only mutations return 403.

---

## Test 7 — Tier chooser and tier/page gating for the tier-test merchant (`sample_pages_enabled` off)

With `sample_pages_enabled` off, the tier-test account still bypasses beta lock, but a regular merchant does not (see Test 8).

### 7.1 Basic Tier
1. In the merchant session, log in again to land on `/choose-tier`.
2. Select **Basic Tier**.
3. Expected `POST /api/merchant/select-tier` returns 200 `{status: "ok", tier: "Basic Tier", redirect: "/dashboard"}`.
4. On `/dashboard`, sidebar should show only: Overview, Alerts, Action Gate, Profit Dashboard, Billing, Settings, Support.
5. Direct access:
   - `/dashboard/overview` → 200.
   - `/dashboard/alerts` → 200.
   - `/dashboard/action-gate` → 200 (PR #15 added `action_gate` to `BETA_READY_PAGE_IDS`).
   - `/dashboard/profit-engine` → 200.
   - `/dashboard/settings` → 200.
   - `/dashboard/billing` → 302 to `/dashboard/settings?tab=billing`.
   - `/dashboard/inventory` → upgrade banner requiring **Vantav Operator**.
   - `/dashboard/not-a-real-page` → 302 to `/dashboard`.

### 7.2 Vantav Operator
1. Return to `/choose-tier`.
2. Select **Vantav Operator**.
3. Sidebar should add: Inventory, Orders, Products, Store Catalog, Commerce Hub, Customers.
4. Direct access:
   - `/dashboard/inventory` → 200.
   - `/dashboard/products` → 200.
   - `/dashboard/predictions` → upgrade banner requiring **Vantav Growth**.

### 7.3 Vantav Growth
1. Select **Vantav Growth**.
2. Sidebar should add Growth pages: Command Center, Monitoring, Predictions, Health Score, Marketing, Automations, Team AI, Product Research, Fulfillment, Returns, Shipments, Suppliers, TikTok Studio, Discounts, Analytics, Mobile.
3. Direct access:
   - `/dashboard/predictions` → 200.
   - `/dashboard/analytics` → 200.
   - `/dashboard/fraud` → upgrade banner requiring **Vantav Scale**.

### 7.4 Vantav Scale
1. Select **Vantav Scale**.
2. Sidebar should add Scale pages: Fraud, Startup Pack, Apps, Reports, Regression Chart.
3. Direct access:
   - `/dashboard/fraud` → 200.
   - `/dashboard/regression_chart` or `/dashboard/regression-chart` → 200.
   - `/dashboard/startup-pack` → 302 to `/dashboard/settings?tab=billing` (unless concierge bundle is attached).

### 7.5 Regression chart
1. With the merchant on **Vantav Scale**, visit `/dashboard/regression_chart?sku=SKU-404-PODS`.
2. The page should **not** show `Unable to load regression data`.
3. The canvas should display a Chart.js line chart with two datasets (white scatter points and a dashed gold OLS trend line) and diagnostics showing `Trend`, `R² Confidence`, and `7-Day Projection`.

**Pass criteria:** every tier selection succeeds, sidebar pages match the tier, direct access to locked pages shows an upgrade banner or redirect (never 500), `action_gate` is visible for Basic Tier, regression chart renders with a line chart.

---

## Test 8 — Beta-lock for a non-tier-test merchant (`sample_pages_enabled` off)

1. Using `curl` with no session cookie (no recording), create a temporary merchant:
   - `POST /api/v1/tenant/register` with `business_name`, `admin_email` (unique), `password_plain`.
   - Expected: `201` JSON `success: true` and `merchant_id`.
2. Log in as the temporary merchant via the UI or `POST /api/v1/auth/login`.
3. Select **Basic Tier** on `/choose-tier`.
4. On `/dashboard`, the sidebar should show **only** the beta-ready pages: Overview, Alerts, Action Gate, Profit Dashboard, Billing, Settings, Support.
5. Direct access tests:
   - `/dashboard/products` → 302 to `/dashboard` (not an upgrade banner, because it is beta-locked and the merchant is not a tier-test account).
   - `/dashboard/predictions` → 302 to `/dashboard`.
   - `/dashboard/fraud` → 302 to `/dashboard`.
6. As admin, call `DELETE /api/admin/merchants/<temp_merchant_id>`.
7. Expected: `200` JSON `status: ok, deleted: <merchant_id>`.
8. Reload `/admin/merchants` and confirm only the three intended accounts remain: `shawn@shawnzyluxe.com`, `engineer@shawnzyluxe.com`, `merchant@vantavcommerce.com`.

**Pass criteria:** non-tier-test merchants cannot see or reach non-beta pages when `sample_pages_enabled` is off; temporary merchant is fully removed.

---

## Test 9 — Public health, monitoring, GDPR webhooks, and 404 handling

Run with `curl` (no recording):

1. Public health:
   - `GET /health` → 200 `HEALTHY`.
   - `GET /api/v1/health` → 200 `HEALTHY`.
2. Role-gated monitoring (admin/engineer cookie):
   - `GET /api/v1/monitoring/health` → 200.
   - `GET /api/v1/monitoring/metrics` → 200.
3. GDPR/webhook endpoints should not 500 when hit without valid signatures; expected 400/401/rejected:
   - `POST /api/v1/webhooks/shopify-orders` → 401 `Missing HMAC`.
   - `POST /api/v1/webhooks/tiktok-orders` → non-500.
   - `POST /api/v1/webhooks/amazon-orders` → non-500.
   - `POST /api/v1/webhooks/shopify-gdpr/*` → 401 `Missing HMAC`.
   - `POST /api/v1/webhooks/shopify/app/uninstalled` → 401.
   - `POST /api/v1/webhooks/stripe-billing` → 400/401.
4. Unknown dashboard route `/dashboard/foobar` → 302 to `/dashboard`.

**Pass criteria:** health endpoints healthy, monitoring endpoints work for authorized roles, webhook endpoints reject unsigned traffic without crashing, unknown dashboard route redirects.

---

## Test 10 — Console errors, broken nav, stale forms, placeholder shells, and security

1. Open the browser console on `/dashboard`, `/admin`, `/engineer`, `/choose-tier`, `/dashboard/regression_chart`, and `/dashboard/action-gate`.
2. Watch for JavaScript errors:
   - `TypeError: Failed to fetch` in `loadMessages` (merchant support widget poll).
   - CSP errors blocking `cdn.jsdelivr.net` on the regression chart.
   - Any `TypeError` from `hero.evidence` or `action_gate` rendering.
3. Check for stale form state:
   - After editing a member, refresh `/admin/merchants` and confirm the row reflects the persisted values.
   - After the merchant selects a tier, the admin members table should reflect the new tier on refresh (not the old cached value from when the Edit modal was opened).
4. Check for placeholder shells:
   - Pages in `PLACEHOLDER_DASHBOARD_PAGE_IDS` should display the placeholder banner from `dashboard/base.html` rather than a blank page.
   - Pages not in the merchant’s tier should show an upgrade banner, not a blank page.
5. Security checks:
   - No API returns the plaintext `SITE_WALL_PASSWORD`, admin password hashes, or session tokens in JSON responses.
   - The `/api/v1/health` endpoint does not leak internal keys.
   - Engineer cannot access admin-only mutations.

**Pass criteria:** no unexpected console errors, no stale form state, no blank placeholder/locked pages, no exposed secrets.

---

## Cleanup

1. In the admin session, set the tier-test merchant back to `Basic Tier`, `sandbox_status=pending`, `live_access_enabled=false`, and clear billing overrides and feature flags.
2. Ensure `sample_pages_enabled` remains **off**.
3. Confirm `/admin/merchants` shows only the three intended accounts; delete any temporary merchant created during Test 8.
4. Stop the recording.

---

## Evidence to capture

- Continuous screen recording of the full audit.
- Screenshots:
  - Admin `/admin` with `sample_pages_enabled` unchecked and platform controls JSON showing `false`.
  - `/admin/merchants` showing channel counts consistent with `/api/admin/stores` and billing usage.
  - `/admin/merchants` Edit modal with tier/sandbox/live/billing/feature-flag changes.
  - Merchant `/choose-tier` and `/dashboard` sidebar for each tier.
  - Merchant `/dashboard/action-gate` loaded for Basic Tier.
  - Locked page upgrade banner and unknown-page redirect.
  - `/dashboard/regression_chart?sku=SKU-404-PODS` with Chart.js line chart and diagnostics.
  - Admin impersonation banner and stop-impersonating flow.
  - Styled admin 404 page.
  - `/admin/chat` thread with unread badge and admin reply.
  - Merchant support widget with admin reply.
  - `/admin/audit` showing relevant events.
  - Engineer dashboard with health/metrics/exceptions and chatbot responses.
  - Engineer `/admin/merchants` showing no Edit button.
  - Public health and webhook `curl` outputs.
  - Browser console error logs (if any).
  - Temporary non-tier-test merchant creation, Basic-tier sidebar, direct `/dashboard/products` redirect, and deletion.
- `annotate_recording` markers at each test start and each significant assertion.
