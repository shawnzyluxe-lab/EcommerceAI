# Vantav live adversarial full-site audit

**Scope:** Runtime end-to-end test of `https://vantavcommerce.com` covering site wall, login, role-based routing, tier chooser, tier/page gating, admin/engineer panels, support chat, public health/monitoring endpoints, GDPR webhooks, and common 404/500/broken-nav regressions.

**Forbidden:** Do not test Stripe checkout. Do not make production state changes (do not actually unlink real stores, pause sync, flip maintenance mode, or broadcast announcements).

**Accounts**
- Admin: `shawn@shawnzyluxe.com` / `VantavMaster2025!` (also `SITE_WALL_PASSWORD` fallback works for admin/engineer).
- Engineer: `engineer@shawnzyluxe.com` / `EngVantav2025!`.
- Tier-test merchant: `merchant@vantavcommerce.com` / `MerchantTest2025!`.

**Environment**
- Live URL: `https://vantavcommerce.com`.
- Session cookie: `vantav_session_token`.
- `auth_login` (`/api/v1/auth/login`, `app.py:4317`) returns `{status, role, merchant_id}` and sets the cookie. It resets the tier-test merchant to `Basic Tier`/`pending`/`live_access=0` each login so the merchant lands on `/choose-tier`.
- Browser: Chrome. Use `--user-data-dir` to run separate sessions for admin/engineer/merchant. If native clicks fail, use `javascript:` address-bar snippets (e.g. `javascript:editMember('tenant_4732bbf6')`, `javascript:saveMember()`).

---

## Setup

1. Verify public health endpoints with `curl` (no recording):
   - `GET https://vantavcommerce.com/health` → 200, `status: HEALTHY`, `database_connected: true`, `generated_storage_write_access: true`.
   - `GET https://vantavcommerce.com/api/v1/health` → 200, same JSON.
   - `GET https://vantavcommerce.com/status` → 302 to `/login` while site wall is enabled (expected).
2. Maximize the Chrome window.
3. Start a single screen recording covering the main audit.
4. `annotate_recording` `setup` `description="Logged into admin, engineer, and merchant sessions; verified public health endpoints"`.

---

## Test 1 — Site wall and login role routing

1. Open `https://vantavcommerce.com/login` in a fresh Chrome profile.
2. Log in as **admin** `shawn@shawnzyluxe.com` / `VantavMaster2025!`.
3. Expected: redirects to `/admin` or `/dashboard` with role `Admin` in the JSON response, no 500, no recaptcha blocking the request.
4. Verify admin-only/admin-allowed routes load:
   - `/admin` → 200 with Admin Dashboard title.
   - `/admin/merchants` → 200.
   - `/admin/chat` → 200.
   - `/admin/audit` → 200.
5. Log in as **engineer** `engineer@shawnzyluxe.com` / `EngVantav2025!` in a second Chrome profile.
6. Expected: role `Engineer`, lands on `/engineer`.
7. Verify engineer routes:
   - `/engineer` → 200.
   - `/admin/merchants` → 200 (read-only; no Edit button).
   - `/admin/audit` → 200.
   - `/admin/chat` → 200 (code allows engineer now, `app.py:3162`).
8. Verify **engineer is denied admin-only actions**:
   - Direct `POST https://vantavcommerce.com/api/admin/stores/<merchant_id>/<platform>/unlink` with engineer cookie → 403.
   - Direct `PATCH https://vantavcommerce.com/api/admin/merchants/<merchant_id>` with engineer cookie → 403.
9. Log in as **merchant** `merchant@vantavcommerce.com` / `MerchantTest2025!` in a third profile.
10. Expected: role `Merchant` and immediate redirect to `/choose-tier` because `_reset_test_merchant_for_tier_testing` sets `sandbox_status=pending` (`app.py:4363`).

**Pass criteria:** all three credentials authorize, roles are correct, admin/engineer reach their dashboards, merchant lands on tier chooser, engineer receives 403 on admin-only mutation endpoints.

---

## Test 2 — Tier chooser and tier page gating

For the merchant test account only, cycle through each tier and verify the sidebar and direct page access match `TIER_PAGE_ACCESS` (`tier_manager.py:214`) and the beta-lock bypass works for tier-test accounts (`dashboard_context.py:304`).

### 2.1 Basic Tier
1. On `/choose-tier`, select **Basic Tier**.
2. Expected: `POST /api/merchant/select-tier` (`app.py:1895`) returns 200 `{status, tier, redirect: ...}` and redirects to `/dashboard`.
3. Sidebar should show only:
   - Overview, Alerts, Profit Dashboard, Action Gate, Billing, Settings, Support.
4. Direct access tests (each should return 200 or an upgrade banner, never 500):
   - `/dashboard/overview` → 200.
   - `/dashboard/alerts` → 200.
   - `/dashboard/profit-engine` → 200.
   - `/dashboard/action-gate` → 200.
   - `/dashboard/settings` → 200.
   - `/dashboard/billing` → 302 to `/dashboard/settings?tab=billing`.
   - `/dashboard/inventory` → upgrade banner (requires Vantav Operator) with text "This module is included in Vantav Operator" and an "Upgrade plan" button (`dashboard_page` lock_content, `app.py:1824`).
   - `/dashboard/command-center` (or `/dashboard/command_center`) → 302 to `/dashboard` because `command_center` is not in `NAV_GROUPS` and also beta-locked? Actually `command_center` is in `valid_pages` and Growth; expect upgrade banner for Growth or redirect? `page_upgrade_target` returns Growth. The route renders upgrade banner if page in DEFAULT_MERCHANT_PAGE_IDS. So expect upgrade banner for Growth. *If `command_center` is not in `valid_pages`, expect 302 to `/dashboard` (record which behavior occurs).*
   - `/dashboard/foobar` → 302 to `/dashboard` (unknown page).

### 2.2 Vantav Operator
1. Return to `/choose-tier` and select **Vantav Operator**.
2. Sidebar should add: Inventory, Orders, Products, Store Catalog, Commerce Hub (Commerce Hub redirects to settings), Customers.
3. Direct access tests:
   - `/dashboard/inventory` → 200.
   - `/dashboard/orders` → 200 or placeholder banner.
   - `/dashboard/products` → 200.
   - `/dashboard/store-catalog` → 200.
   - `/dashboard/customers` → 200 or placeholder banner.
   - `/dashboard/predictions` (Growth) → upgrade banner for Vantav Growth.
   - `/dashboard/fraud` (Scale) → upgrade banner for Vantav Scale.

### 2.3 Vantav Growth
1. Select **Vantav Growth**.
2. Sidebar should add Growth/Operations pages: Command Center, Monitoring, Predictions, Health Score, Marketing, Automations, Team AI, Product Research, Fulfillment, Returns, Shipments, Suppliers, TikTok Studio, Discounts, Analytics, Mobile.
3. Direct access tests:
   - `/dashboard/predictions` → 200 or placeholder banner.
   - `/dashboard/analytics` → 200 or placeholder banner.
   - `/dashboard/fraud` (Scale) → upgrade banner for Vantav Scale.

### 2.4 Vantav Scale
1. Select **Vantav Scale**.
2. Sidebar should add Scale pages: Fraud, Startup Pack, Apps, Reports, Regression Chart.
3. Direct access tests:
   - `/dashboard/fraud` → 200 or placeholder banner.
   - `/dashboard/startup-pack` → 302 to `/dashboard/settings?tab=billing` *unless* concierge bundle is attached (no concierge expected for test merchant).
   - `/dashboard/regression-chart` or `/regression-chart?sku=SKU-404-PODS` → 200 and chart canvas visible (or blank due to CSP bug from earlier runs; note if it is).

### 2.5 Tier-test beta-lock bypass
1. With `sample_pages_enabled` off (default), confirm the merchant test account can still see and reach pages in `BETA_LOCKED_PAGE_IDS` (e.g. `/dashboard/products`, `/dashboard/predictions`) when tier allows. Non-tier-test merchants would be redirected from those pages (`app.py:1809`). This confirms the bypass works.

**Pass criteria:** every tier selection succeeds, sidebar matches the tier, direct access to locked pages shows an upgrade/redirect and never 500, and the tier-test account bypasses the beta lock for paid pages.

---

## Test 3 — Admin panel

In the admin session:

1. `/admin` loads with KPIs (total members, active sessions, paid accounts, unread support, connected stores, Stripe available) and recent activity (`dashboard/admin.html`).
2. `/admin/merchants` loads with a Stripe balance card and member rows. The tier-test merchant `merchant@vantavcommerce.com` is present.
3. Click **Edit** for the tier-test merchant (or `javascript:editMember('tenant_4732bbf6')`).
   - Change tier, sandbox status, live access, and toggle a per-page feature flag (e.g. `products` ON).
4. Save and reload; verify the merchant row reflects the new feature count.
5. In the merchant session, refresh `/dashboard`; verify the corresponding sidebar link appears/reappears and direct route is reachable when ON and blocked when OFF.
6. `/admin/chat` loads, thread list contains `merchant@vantavcommerce.com` after the merchant sends a message (see Test 4).
7. `/admin/audit` loads and shows `admin.login` and `platform_control.set`/`store.unlink`/merchant update events.
8. Platform controls:
   - Load `/admin` or `/engineer`, verify `loadPlatformControls()` fetches `/api/admin/platform-controls` and the checkboxes reflect the server state.
   - *Do not save changes* in production; only verify the GET succeeds.
9. Store unlink UI:
   - Confirm the `Unlink` button is present in the Connected stores table (`admin.html:116`).
   - Do **not** click Unlink on a real store. If a safe test merchant has a connected store, attempt it; otherwise skip the destructive click and report.

**Pass criteria:** admin routes render real data, member editing works, feature flags take effect, audit log loads.

---

## Test 4 — Engineer panel

In the engineer session:

1. `/engineer` loads and fetches:
   - `/api/v1/monitoring/health` → 200.
   - `/api/v1/monitoring/metrics` → 200.
   - `/api/engineer/exceptions` → 200 (engineer-only).
2. KPIs (system status, request count, P95 latency, error rate, exceptions 24h, connected stores) populate.
3. Engineer Assistant chatbot accepts a command (e.g. "health") and returns a response via `/api/engineer/chat` (or the endpoint used by `engineer.html`).
4. Read-only member access: `/admin/merchants` loads but the **Edit** button is hidden (`admin_merchants.html:101` only renders `{% if is_admin %}`).
5. Denied admin-only actions: attempt to call `saveMember()` or `unlinkStore()`; confirm 403.

**Pass criteria:** engineer sees system health/metrics/exceptions, can read member list, cannot mutate members or unlink stores.

---

## Test 5 — Support chat two-way

1. In the **merchant** session (on `/dashboard`), click the floating support widget (bottom-left ✉) or call `toggleSupport()`.
2. Send a message: type "Audit test message" and click Send / `sendSupportMessage()`.
3. Expected: `POST /api/v1/chat/message` returns 201/200 and the message appears in the widget.
4. In the **admin** session, open `/admin/chat`.
5. Verify the merchant thread appears with an unread badge.
6. Open the thread (`loadThread('tenant_4732bbf6')`) and reply: "Admin reply from audit".
7. In the **merchant** session, wait for the 8-second poll (`loadMessages` in `base.html:486`) and verify the admin reply appears.

**Pass criteria:** merchant → admin and admin → merchant messages both visible within ~10 seconds.

---

## Test 6 — Public health/monitoring endpoints and GDPR webhooks

Run with `curl` (no recording):

1. Public health:
   - `GET /health` → 200 `HEALTHY`.
   - `GET /api/v1/health` → 200 `HEALTHY`.
2. Role-gated monitoring (with admin or engineer cookie):
   - `GET /api/v1/monitoring/health` → 200.
   - `GET /api/v1/monitoring/metrics` → 200.
3. GDPR/webhook endpoints should not 500 when hit without valid signatures; expected 400/401/rejected:
   - `POST /api/v1/webhooks/shopify-orders` (no HMAC) → 400.
   - `POST /api/v1/webhooks/tiktok-orders` (no signature) → 400.
   - `POST /api/v1/webhooks/amazon-orders` (no signature) → 400.
   - `POST /api/v1/webhooks/shopify-gdpr/customers/data_request` (no signature) → 400.
   - `POST /api/v1/webhooks/shopify-gdpr/customers/redact` (no signature) → 400.
   - `POST /api/v1/webhooks/shopify-gdpr/shop/redact` (no signature) → 400.
   - `POST /api/v1/webhooks/shopify/app/uninstalled` (no signature) → 400.
   - `POST /api/v1/webhooks/stripe-billing` (no Stripe-Signature) → 400.
4. `GET` on those webhook endpoints (if allowed) returns method-not-allowed or a non-500 response; capture the code.

**Pass criteria:** health endpoints report healthy, monitoring endpoints respond for admin/engineer, webhook endpoints reject unsigned traffic without crashing.

---

## Test 7 — 404, broken nav, and empty shells

1. Hit an invalid dashboard page `/dashboard/not-a-real-page`:
   - Expected 302 to `/dashboard` because it is not in `valid_pages` (`app.py:1792`).
2. Hit an invalid admin page `/admin/not-real`:
   - Expected 404.
3. Check for JavaScript console errors on `/dashboard`, `/admin`, `/engineer`, `/choose-tier`.
4. Check for empty shells: pages in `PLACEHOLDER_DASHBOARD_PAGE_IDS` should display the placeholder banner (`show_placeholder_banner`, `dashboard/base.html:176`) rather than a blank page.
5. Check for missing/broken sidebar links:
   - Confirm `command_center` (Command Center) is missing from `NAV_GROUPS` even though it is a valid page (`dashboard_context.py:778`); flag if this is intentional or a navigation gap.
   - Confirm `Commerce Hub` redirects to Settings as expected (`app.py:1785`).

**Pass criteria:** no 500s, unknown dashboard pages redirect, invalid admin routes 404, placeholder pages show banners, and no broken nav links.

---

## Cleanup

1. As admin, set the tier-test merchant back to **Basic Tier**, `sandbox_status=pending`, `live_access_enabled=false`, and clear any feature flags toggled during testing.
2. Alternatively, log the merchant out; the next login will reset it because `_reset_test_merchant_for_tier_testing` runs in `auth_login`.
3. Delete any support messages sent during the audit if easily possible (otherwise leave; they are harmless).
4. Stop the recording.

---

## Evidence to capture

- Continuous screen recording of the browser audit.
- Screenshots:
  - Admin dashboard, `/admin/merchants`, Edit modal.
  - Engineer dashboard and `/admin/merchants` read-only view.
  - Merchant `/choose-tier` and `/dashboard` sidebar for each tier.
  - Upgrade/redirect page on a locked page.
  - Support chat widget and `/admin/chat` thread.
  - Public health endpoint JSON and webhook response codes.
- `annotate_recording` markers at each test start and each significant assertion.
- Console error logs from the browser (if any).
