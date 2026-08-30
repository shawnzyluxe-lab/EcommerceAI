---
name: public-testing-audit
description: Live public-testing adversarial audit for the Vantav dashboard on https://vantavcommerce.com.
---

# Vantav Public Testing Audit

## Accounts

- Admin: `shawn@shawnzyluxe.com` / `VantavMaster2025!`
- Engineer: `engineer@shawnzyluxe.com` / `EngVantav2025!`
- Tier-test merchant: `merchant@vantavcommerce.com` / `MerchantTest2025!`
- Temporary non-tier-test merchant: create with `POST /api/v1/tenant/register`, delete with `DELETE /api/admin/merchants/<merchant_id>`.

## Managed Chrome quirks

- The `computer` tool coordinate space is 1024x768, but screenshots are 1600x1200, so coordinates scale by ~1.56x.
- Native `left_click` on small inline buttons or the beta-login submit button can fail to register. Prefer `Ctrl+L` address-bar navigation and `javascript:` snippets:
  - Beta login: `javascript:document.getElementById('email').value='<email>';document.getElementById('password').value='<password>';setTimeout(()=>document.getElementById('btn').click(),300);`
  - Tier selection: `javascript:fetch('/api/merchant/select-tier',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({tier:'Vantav Operator'})}).then(r=>r.json()).then(j=>{alert(JSON.stringify(j)); window.location='/dashboard';});`
  - Merchant support widget: `javascript:document.getElementById('support-input').value='...'; sendSupportMessage();`
  - Admin chat reply: `javascript:document.getElementById('message-text').value='...'; sendMessage();`
- The merchant support widget can be toggled with `javascript:toggleSupport();`.

## Switches and state

- `sample_pages_enabled` must be **off** for the public-testing audit; verify with `GET /api/admin/platform-controls`.
- The tier-test merchant resets to `Basic Tier`/`pending` on every login and lands on `/choose-tier`.
- `PATCH /api/admin/merchants/<id>` replaces the entire `feature_flags` dict (Default/omitted flags are removed).
- `PATCH /api/admin/merchants/<id>` now safely handles empty strings for numeric fields (`max_authorized_seats`, `metered_usage_units`, `accrued_invoice_value`) by defaulting empty values to the tier default or 0 and no longer throws a 500.
- `PATCH /api/admin/merchants/<id>` clears `current_plan` when an empty string is sent; `_canonical_tier('')` no longer forces `Vantav Operator` for the billing `current_plan` field.
- `DELETE /api/engineer/exceptions?confirm=1&id=<id>` lets an engineer remove a stale exception row.

## Common checks

- `GET /health` and `GET /api/v1/health` should return `HEALTHY`.
- `GET /api/v1/monitoring/health` and `/api/v1/monitoring/metrics` require an admin or engineer cookie.
- `GET /api/engineer/exceptions` is engineer-only and should not contain fresh `CRITICAL` errors.
- `GET /api/admin/impersonate/<merchant_id>` with `Accept: text/html` redirects to `/dashboard` with an impersonation banner.
- `/admin/<any-path>` unknown returns a styled `Page not found` with a `Return to Admin Home` button.
- Public webhook/GDPR endpoints reject unsigned requests with 401/400 and do not 500.
- Regression chart: `/dashboard/regression_chart?sku=SKU-404-PODS` should render a Chart.js line chart and diagnostics.
- Temporary non-tier-test merchants see only beta-ready pages (`overview`, `alerts`, `action_gate`, `profit_engine`, `billing`, `settings`, `support`) and are redirected from paid/locked pages.

## Forbidden

- Do not run the live Stripe micro-charge test.
- Do not merge PR #15.
- Do not unlink real stores, pause sync, flip maintenance mode, or broadcast real announcements.
- If a temporary merchant is created, delete it so only `shawn@shawnzyluxe.com`, `engineer@shawnzyluxe.com`, and `merchant@vantavcommerce.com` remain.

## Devin Secrets Needed

- None for this audit; credentials are hardcoded test accounts.
