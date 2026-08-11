"""Stripe billing integration for Prometheus OS beta."""
import os
import json
import logging
import stripe

from models import db, SaaSBilling, MerchantProfile

logger = logging.getLogger(__name__)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
PRICE_BETA_MONTHLY = os.environ.get("STRIPE_PRICE_BETA_MONTHLY", "")
PRICE_BETA_STARTUP = os.environ.get("STRIPE_PRICE_BETA_STARTUP", "")
PRICE_STARTUP_ADDON = os.environ.get("STRIPE_PRICE_STARTUP_ADDON", "")

# Add-on price IDs (created 2026-08-11)
PRICE_CUSTOM_BRAND_BUILD_SETUP = os.environ.get("STRIPE_PRICE_CUSTOM_BRAND_BUILD_SETUP", "")
PRICE_CUSTOM_BRAND_BUILD_MONTHLY = os.environ.get("STRIPE_PRICE_CUSTOM_BRAND_BUILD_MONTHLY", "")
PRICE_SEO_SETUP = os.environ.get("STRIPE_PRICE_SEO_SETUP", "")
PRICE_SEO_MONTHLY = os.environ.get("STRIPE_PRICE_SEO_MONTHLY", "")
PRICE_EMAIL_SETUP = os.environ.get("STRIPE_PRICE_EMAIL_SETUP", "")
PRICE_CURATED_AD_PLAN_MONTHLY = os.environ.get("STRIPE_PRICE_CURATED_AD_PLAN_MONTHLY", "")

ADDON_PRICE_MAP = {
    "custom_brand_build": {
        "setup": PRICE_CUSTOM_BRAND_BUILD_SETUP,
        "monthly": PRICE_CUSTOM_BRAND_BUILD_MONTHLY,
    },
    "seo": {
        "setup": PRICE_SEO_SETUP,
        "monthly": PRICE_SEO_MONTHLY,
    },
    "email_setup": {
        "setup": PRICE_EMAIL_SETUP,
    },
    "curated_ad_plan": {
        "monthly": PRICE_CURATED_AD_PLAN_MONTHLY,
    },
}


def _ensure_configured():
    if not stripe.api_key:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")


def get_or_create_customer(email, name, merchant_id):
    """Find or create a Stripe Customer linked to a merchant."""
    _ensure_configured()
    try:
        existing = stripe.Customer.list(email=email, limit=1)
        if existing and existing.data:
            customer = existing.data[0]
            if customer.metadata.get("merchant_id") != merchant_id:
                stripe.Customer.modify(
                    customer.id,
                    metadata={"merchant_id": merchant_id},
                )
            return customer.id

        customer = stripe.Customer.create(
            email=email,
            name=name or email,
            metadata={"merchant_id": merchant_id},
        )
        return customer.id
    except stripe.error.StripeError as e:
        logger.error(f"[Stripe] Customer error: {e}")
        raise


def create_checkout_session(merchant_id, email, name, include_startup_addon=False, plan="beta", success_url=None, cancel_url=None):
    """Create a Stripe Checkout session for the beta or beta+startup plan."""
    _ensure_configured()
    selected_tier = "Beta Tier"
    chosen_plan = plan.lower().strip()

    if chosen_plan == "beta_startup":
        if not PRICE_BETA_STARTUP:
            raise RuntimeError("STRIPE_PRICE_BETA_STARTUP is not configured")
        selected_tier = "Beta + Startup Pack"
        line_items = [{"price": PRICE_BETA_STARTUP, "quantity": 1}]
    else:
        if not PRICE_BETA_MONTHLY:
            raise RuntimeError("STRIPE_PRICE_BETA_MONTHLY is not configured")
        selected_tier = "Beta Tier"
        line_items = [{"price": PRICE_BETA_MONTHLY, "quantity": 1}]
        if include_startup_addon and PRICE_STARTUP_ADDON:
            line_items.append({"price": PRICE_STARTUP_ADDON, "quantity": 1})

    profile = MerchantProfile.query.get(merchant_id)
    if not profile:
        raise ValueError(f"Merchant {merchant_id} not found")

    customer_id = get_or_create_customer(email, name, merchant_id)

    # Persist customer link immediately so the webhook can match later.
    billing = SaaSBilling.query.get(merchant_id)
    if not billing:
        billing = SaaSBilling(merchant_id=merchant_id)
        db.session.add(billing)
    billing.stripe_customer_id = customer_id
    billing.current_plan = selected_tier
    db.session.commit()

    params = {
        "customer": customer_id,
        "mode": "subscription",
        "payment_method_types": ["card"],
        "line_items": line_items,
        "metadata": {
            "merchant_id": merchant_id,
            "selected_tier": selected_tier,
            "startup_addon": str(chosen_plan == "beta_startup" or include_startup_addon).lower(),
            "plan_choice": chosen_plan,
        },
        "subscription_data": {
            "metadata": {
                "merchant_id": merchant_id,
                "selected_tier": selected_tier,
            }
        },
        "allow_promotion_codes": True,
        "success_url": success_url or "https://vantavcommerce.com/dashboard/billing?checkout=success",
        "cancel_url": cancel_url or "https://vantavcommerce.com/subscribe?canceled=1",
        "automatic_tax": {"enabled": False},
        "managed_payments": {"enabled": False},
    }

    session = stripe.checkout.Session.create(**params)
    return session.url, session.id, customer_id


def create_customer_portal_session(merchant_id, return_url=None):
    """Create a Stripe Billing Portal session for an existing customer."""
    _ensure_configured()
    billing = SaaSBilling.query.get(merchant_id)
    if not billing or not billing.stripe_customer_id:
        raise ValueError("No Stripe customer linked to this account")

    session = stripe.billing_portal.Session.create(
        customer=billing.stripe_customer_id,
        return_url=return_url or "https://vantavcommerce.com/dashboard/billing",
    )
    return session.url


def get_public_key():
    return STRIPE_PUBLISHABLE_KEY


def handle_webhook(payload, sig_header, secret):
    """Verify and parse a Stripe webhook event."""
    if not secret:
        logger.warning("STRIPE_WEBHOOK_SECRET not configured; skipping verification")
        return json.loads(payload)
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
        return event
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"[Stripe Webhook] Invalid signature: {e}")
        raise
