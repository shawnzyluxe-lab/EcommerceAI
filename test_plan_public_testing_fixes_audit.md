# Vantav public-testing fixes live audit

**Scope:** Runtime E2E audit of `https://vantavcommerce.com` focused on the two deployed fixes: safe `PATCH /api/admin/merchants/<id>` empty numeric/current_plan fields, and `DELETE /api/engineer/exceptions?confirm=1&id=<id>` for clearing stale TEARDOWN logs. Also verify role sidebars, tier chooser reset/gating, platform controls, and cleanup.

**Accounts**
- Admin: `shawn@shawnzyluxe.com` / `VantavMaster2025!`
- Engineer: `engineer@shawnzyluxe.com` / `EngVantav2025!`
- Tier-test merchant: `merchant@vantavcommerce.com` / `MerchantTest2025!`

**Environment**
- Live URL: `https://vantavcommerce.com`.
- `sample_pages_enabled` must remain **off**.
- Forbidden: Stripe micro-charge, destructive production changes, leaving extra merchant profiles.

---

## Setup (not recorded)

1. `curl` public health:
   - `GET https://vantavcommerce.com/health` → 200 `HEALTHY`.
   - `GET https://vantavcommerce.com/api/v1/health` → 200 `HEALTHY`.
2. `curl` platform controls with admin cookie after login:
   - `GET /api/admin/platform-controls` → `{"sample_pages_enabled":false,"maintenance_mode":false,"global_sync_paused":false}`.
3. Launch three maximized Chrome incognito windows (admin, engineer, merchant).
4. Start a single screen recording covering the audit.

---

## Test 1 — Admin login and Admin-only sidebar

1. Log in as admin at `/login`.
2. Expected JSON response: `{"status":"AUTHORIZED","role":"Admin"}`, redirect to `/admin`.
3. On `/admin`, inspect the left sidebar.
4. **Pass criteria:** sidebar contains exactly one group labeled **Admin** with links: **Admin Home**, **Members**, **Audit Log**, **Support Chat**. No merchant-tier groups (Overview, Intelligence, Operations, Growth, Catalog, Settings) should be present.

---

## Test 2 — Engineer login and Engineer-only sidebar

1. In a second Chrome profile, log in as engineer at `/login`.
2. Expected JSON response: `{"status":"AUTHORIZED","role":"Engineer"}`, redirect to `/engineer`.
3. On `/engineer`, inspect the left sidebar.
4. **Pass criteria:** sidebar contains exactly one group labeled **Engineer** with links: **Engineer Home**, **Members**, **Audit Log**, **Support Chat**. No merchant-tier groups should be present.
5. In the Engineer Assistant chat, send `summary`. Expected: a JSON response with `reply` summarizing system health.

---

## Test 3 — `PATCH /api/admin/merchants/<id>` safe empty fields

1. In the admin session, open `/admin/merchants`.
2. Click **Edit** for `tenant_4732bbf6`.
3. Set **Tier** to `Basic Tier`, **Sandbox** to `pending`, uncheck **Live access**, clear **Current plan**, clear **Max seats**, clear **Metered usage units**, clear **Accrued invoice value**, and set `products` to **Default**.
4. Save.
5. **Pass criteria:** the request returns 200 JSON; the row updates to `Basic Tier / Pending`, `current_plan` is empty/null, `max_authorized_seats` equals the Basic tier default (`1`), `metered_usage_units` is `0`, `accrued_invoice_value` is `0`, and no 500/CRITICAL exception is logged.
6. Verify via `GET /api/admin/merchants` response for `tenant_4732bbf6`:
   - `account_tier`: `Basic Tier`
   - `sandbox_status`: `pending`
   - `live_access`: `false`
   - `current_plan`: empty string or null
   - `max_authorized_seats`: `1`
   - `metered_usage_units`: `0`
   - `accrued_invoice_value`: `0.0`
   - `feature_flags`: `{}`

---

## Test 4 — `DELETE /api/engineer/exceptions` and clean exception list

1. `curl` login as engineer to get a cookie.
2. `GET /api/engineer/exceptions` and note any `CRITICAL` TEARDOWN exception `id`.
3. `DELETE /api/engineer/exceptions?confirm=1&id=<id>` → expected `{"deleted":1}` and HTTP 200.
4. `GET /api/engineer/exceptions` again.
5. **Pass criteria:** exceptions array is empty (or contains only non-CRITICAL, non-TEARDOWN entries). No `CRITICAL` `TEARDOWN` exceptions remain.

---

## Test 5 — Tier-test merchant reset and tier/page gating

1. Log in as `merchant@vantavcommerce.com` in a third incognito window.
2. **Pass criteria:** login resets the merchant to `Basic Tier`/`pending` and redirects to `/choose-tier`.
3. Select **Vantav Operator**. Expected redirect to `/dashboard`.
4. Sidebar should show Operator pages: Overview, Intelligence (Alerts, Action Gate, Profit Dashboard), Operations (Inventory, Orders, Store Catalog, Commerce Hub, Customers), Settings.
5. Directly visit `/dashboard/team-ai` (a Growth-tier page). Expected: upgrade banner or redirect, not 500.
6. Select **Vantav Growth**. Sidebar should add Growth pages (Command Center, Monitoring, Predictions, Health Score, Marketing, Automations, Team AI, Product Research, Fulfillment, Returns, Shipments, Suppliers, TikTok Studio, Discounts, Analytics, Mobile).
7. Directly visit `/dashboard/team-ai`. Expected: page loads (Growth tier allows it; tier-test bypasses beta lock).
8. Directly visit `/dashboard/fraud` (Scale-only). Expected: upgrade banner requiring **Vantav Scale**, not 500.
9. Select **Vantav Scale**. Directly visit `/dashboard/fraud`. Expected: page loads.
10. Visit `/dashboard/regression_chart?sku=SKU-404-PODS`. Expected: Chart.js line chart renders with diagnostics (trend, R², 7-day projection).
11. Log the merchant out and back in. **Pass criteria:** lands on `/choose-tier` again (reset to Basic/pending).

---

## Test 6 — Temporary non-tier-test merchant beta-lock and deletion

1. `curl` `POST /api/v1/tenant/register` with a unique email to create a temporary merchant.
2. Log in as the temporary merchant; select **Basic Tier**.
3. On `/dashboard`, sidebar should show only beta-ready pages: Overview, Alerts, Action Gate, Profit Dashboard, Billing, Settings, Support.
4. Direct `/dashboard/products` (locked non-beta and above tier). Expected: redirect to `/dashboard`, not upgrade banner (non-tier-test account).
5. As admin, `DELETE /api/admin/merchants/<temp_merchant_id>` → `{"status":"ok","deleted":"<id>"}`.
6. `GET /api/admin/merchants` confirms only three profiles remain.

---

## Test 7 — Platform controls remain off

1. In admin session, open `/admin`.
2. **Pass criteria:** Platform controls card shows all checkboxes unchecked (`Pause all marketplace syncs`, `Maintenance mode banner`, `Show non-beta modules to merchants`).
3. `GET /api/admin/platform-controls` returns `{"sample_pages_enabled":false,"maintenance_mode":false,"global_sync_paused":false}`.

---

## Cleanup

1. Ensure `tenant_4732bbf6` is `Basic Tier / pending / live_access=false` with no feature overrides.
2. Delete any temporary merchant created in Test 6.
3. `GET /api/admin/merchants` confirms exactly three profiles: `shawn@shawnzyluxe.com`, `engineer@shawnzyluxe.com`, `merchant@vantavcommerce.com`.
4. Stop recording.
