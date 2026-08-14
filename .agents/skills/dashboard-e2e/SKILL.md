---
name: dashboard-e2e
description: End-to-end testing guide for the Prometheus OS white-theme dashboard.
---

# Dashboard E2E Testing

## Starting the app

```bash
cd /home/ubuntu/repos/EcommerceAI
source .venv/bin/activate
python app.py
```

The dev server runs on `http://127.0.0.1:3000`.

## Authentication

- If `SITE_WALL_PASSWORD` is set, the lock screen at `/` requires that password.
- If `.env` is empty or missing, the wall is disabled and `/dashboard` is public.
- Valid wall login creates a `vantav_session_token` cookie tied to `merchant_shawn_01`.

## Quick verification checklist

1. `/` returns the Prometheus OS lock screen (or redirects to `/dashboard` if wall disabled).
2. `/dashboard` loads the white Overview with KPIs:
   - Revenue today: `$4,582`
   - True profit: `$1,394`
   - Orders: `61`
   - Needs attention: `3`
3. Sidebar navigation highlights the active page in gold.
4. Key routes return 200:
   - `/dashboard/orders`
   - `/dashboard/commerce-hub`
   - `/dashboard/products`
   - `/dashboard/alerts`
   - `/dashboard/settings`
   - `/dashboard/health-score`
   - `/dashboard/command-center`
5. `/dashboard/commerce-hub` displays cards for Shopify, Amazon, TikTok Shop, eBay, WooCommerce, Walmart, BigCommerce.
6. Narrow viewport reflows to a single column; the hamburger toggles the mobile sidebar.
7. `python -m pytest -q` passes.

## Responsive testing

Chrome launches maximized in the managed environment. To test narrow viewports reliably, resize the display first:

```bash
xrandr --output VNC-0 --mode 800x600
```

Restore after testing:

```bash
xrandr --output VNC-0 --mode 1600x1200
```

## Common pitfalls

- Dashboard templates use dict objects. Jinja `{{ obj.items }}` resolves to the built-in dict method. Use `{{ obj['items'] }}` for keys that collide with dict methods (`items`, `keys`, `values`, `update`, `get`).
- `dashboard_context.py` mutates `BRIEFING` from DB on startup. The `context()` function now returns a fresh copy so KPIs always match `PROFIT_BREAKDOWN`.
- The `/dashboard/<page>` whitelist uses hyphenated slugs (`commerce-hub`, `profit-engine`, `health-score`). Links in templates should match the whitelist slugs, not the underscore template filenames.

## Marketing demo recording

- The live site (`https://vantavcommerce.com`) does **not** currently deploy `/demo` or the authenticated dashboard; record demos against `http://127.0.0.1:3000`.
- Start the local server with a test reCAPTCHA key so sandbox logins work:
  ```bash
  env -u RECAPTCHA_SITE_KEY -u RECAPTCHA_SECRET_KEY \
      RECAPTCHA_SITE_KEY="6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI" \
      RECAPTCHA_SECRET_KEY="" \
      SESSION_COOKIE_SECURE=false \
      .venv/bin/python app.py
  ```
- `verify_captcha_v3` returns `1.0` when `RECAPTCHA_SECRET_KEY` is empty, so the Google test key badge can be hidden via `document.querySelector('.grecaptcha-badge').style.display='none'` if it appears in the recording.
- Seed `merchant_ivor_demo` (or another sandbox merchant) with `ivonderhaff@gmail.com` / `Pqk57Qa9Weo` and matching orders/channels before recording so the dashboard KPIs read `$4,582` revenue and `$1,394` net profit.
- The `computer` `left_click` action may not register on page elements in this environment; use `Ctrl+L` address-bar navigation and `browser_console`/`Return` for form submission and in-page generators.

## Live deployed E2E testing (Render / https://vantavcommerce.com)

- Use the test merchant `test_rules_engine@example.com` / `TestPass123!`.
- The beta login flow collects a reCAPTCHA token if `grecaptcha` is present but continues without it; the live endpoint does not enforce a captcha score.
- `POST /api/v1/forecast/cron` returns SKU-level forecasts and, if a source-id bucket is free, creates `inventory_runout`/`reorder` alerts via `rules_engine.evaluate_products`.
- `GET /api/analytics/channels?days=30` returns per-channel true-profit JSON; the dashboard Overview renders it in the **Channel Performance** table.
- `/dashboard/action-gate` lists pending actions with evidence (confidence, expected impact, market) and **Approve/Modify/Deny** buttons.
- `POST /api/actions/<id>/approve` executes the action and moves it to the **Recent Decisions** table.
- `POST /api/actions/<id>/verify` returns `before_metrics`/`after_metrics`; note that `action_gate.action_to_dict` currently omits `verification_report` and `verified_at`, so the Action Gate UI does not surface the verification text.
- To make POST API calls visible in a recording, inject a temporary DOM overlay from `browser_console` and display the JSON response before navigating away.

## Regression chart CSP

- `/regression-chart?sku=SKU-404-PODS` loads Chart.js from `https://cdn.jsdelivr.net/npm/chart.js`.
- The CSP `script-src` in `monitoring.py` must include `https://cdn.jsdelivr.net`; otherwise the canvas stays blank even though the API returns the DEGRADED text.
- To test the chart end-to-end locally, run the app with a CSP middleware that allows the CDN or temporarily patch `monitoring.py` during testing only.
- Use `seed_regression_sku.py` (or a temporary deterministic seed) to populate `SKU-404-PODS` data for the target merchant; `sqlite:///shawnzyluxe.db` resolves to `instance/shawnzyluxe.db` in this repo.
