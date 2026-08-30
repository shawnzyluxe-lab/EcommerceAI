# Vantav EcommerceAI — Session Handoff

## Current State
- Repo: `https://github.com/shawnzyluxe-lab/EcommerceAI`
- Branch: `devin/profit-feed`
- Open PR: #15 — https://github.com/shawnzyluxe-lab/EcommerceAI/pull/15
- Live site: https://vantavcommerce.com
- Render service: `srv-d9s52rqfngtc73ej57pg`
- Latest deploy: `dep-da99aqon74is73fafqm0` (HEALTHY)

## What is left to do before PR #15 can merge
1. Run a live Stripe micro-charge for a disposable merchant.
2. Verify the payment flips `account_tier`, `sandbox_status='approved'`, `live_access_enabled=1`, and lands the merchant on the paid-tier dashboard.
3. Refund the charge and clean up the test customer/subscription.
4. Confirm the tier upgrade persists after logout/login.
5. Merge PR #15.

## Access the other Devin account needs
- GitHub access to `shawnzyluxe-lab/EcommerceAI` (already confirmed).
- Render API key for the same workspace so it can trigger deploys and read/update env vars (`RENDER_API_KEY`).
- The live platform test account credentials below.

## Deploy workflow (must follow every time)
Render cannot pull from the private repo because the Render GitHub app is not connected. The standard flow is:
1. Make the repo public via GitHub API.
2. Trigger the Render deploy for the desired commit.
3. Poll deploy status until `live`.
4. Verify `GET https://vantavcommerce.com/health` returns `HEALTHY`.
5. Revert the repo to private via GitHub API.

## Live test accounts
| Role | Email | Password | Notes |
|------|-------|----------|-------|
| Admin | `shawn@shawnzyluxe.com` | `VantavMaster2025!` | Master admin; lands on `/admin` |
| Engineer | `engineer@shawnzyluxe.com` | `EngVantav2025!` | Ops panel at `/engineer` |
| Tier tester | `merchant@vantavcommerce.com` | `MerchantTest2025!` | Resets to `/choose-tier` on every login; paid tiers bypass checkout |

## Stripe micro-charge test plan
1. Create a **disposable merchant** via `POST /api/v1/tenant/register` (do not reuse `merchant@vantavcommerce.com`; that account is reserved for tier UI testing).
2. Choose the desired paid tier on the new merchant.
3. Create a Stripe Checkout Session using the correct price ID from Render env:
   - `STRIPE_PRICE_OPERATOR_MONTHLY`
   - `STRIPE_PRICE_GROWTH_MONTHLY`
   - `STRIPE_PRICE_SCALE_MONTHLY`
   Use a small amount such as `$0.50` (or the plan's configured price).
4. Complete the payment in Stripe test mode or with a real card.
5. Wait for/force the `stripe_billing_webhook` to fire and confirm in the DB:
   - `merchant_profiles.account_tier` = selected tier
   - `merchant_profiles.sandbox_status` = `approved`
   - `merchant_profiles.live_access_enabled` = `1`
6. Log in as the disposable merchant and confirm the dashboard shows the paid-tier pages.
7. Refund the Stripe charge from the Stripe dashboard and delete the test customer/subscription.
8. Delete or neutralize the disposable merchant profile.

## Important clean-up / security
- **Rotate exposed secrets.** During the previous session the full Render env var list (including `SHOPIFY_CLIENT_SECRET`, `SHOPIFY_ACCESS_TOKEN`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `DATABASE_URL` password, `RLS_APP_USER_PASSWORD`, and `TWILIO_AUTH_TOKEN`) was pulled into the session log. Rotate these in Render and the respective provider dashboards, then redeploy, before public beta begins.
- The `merchant@vantavcommerce.com` tier-test account is left in `Basic Tier` / `sandbox_status=pending`; it will redirect to `/choose-tier` on next login.

## Known low-priority issues (not blockers)
- TikTok/Amazon unsigned webhooks reject with `400 Missing merchant_id` instead of `401 Missing HMAC` like the Shopify GDPR endpoints. They do not 500; just inconsistent error ordering.
- Tests emit SQLAlchemy `Query.get()` deprecation warnings (cosmetic).

## Files that changed most recently
- `app.py` — site wall, admin/engineer panels, tier chooser, chat, webhooks
- `dashboard_context.py` — nav gating, feature flags, placeholder banner
- `tier_manager.py` — tier logic and test-account detection
- `templates/dashboard/*.html` — admin, engineer, and merchant dashboard UIs
