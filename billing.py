"""Stripe billing integration for Vantav."""
import os
import json
import logging
from typing import Dict, Any
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import stripe

from models import db, SaaSBilling, MerchantProfile
from tier_manager import TIER_PRICE_ENV, PLAN_TO_TIER

logger = logging.getLogger(__name__)


def _add_qs_param(url, key, value):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    qs[key] = [value]
    return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))


stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", os.environ.get("STRIPE_LIVE_SECRET_KEY", ""))
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", os.environ.get("STRIPE_LIVE_PUBLISHABLE_KEY", ""))

# Backwards-compatible legacy price IDs.
PRICE_BETA_MONTHLY = os.environ.get("STRIPE_PRICE_BETA_MONTHLY", "")
PRICE_BETA_STARTUP = os.environ.get("STRIPE_PRICE_BETA_STARTUP", "")
PRICE_STARTUP_ADDON = os.environ.get("STRIPE_PRICE_STARTUP_ADDON", "")

# Vantav commercial tiers
PRICE_OPERATOR_MONTHLY = os.environ.get("STRIPE_PRICE_OPERATOR_MONTHLY", "")
PRICE_GROWTH_MONTHLY = os.environ.get("STRIPE_PRICE_GROWTH_MONTHLY", "")
PRICE_SCALE_MONTHLY = os.environ.get("STRIPE_PRICE_SCALE_MONTHLY", "")
# Concierge Bundle is a one-time add-on fee charged on the first invoice.
PRICE_CONCIERGE_BUNDLE = os.environ.get("STRIPE_PRICE_CONCIERGE_BUNDLE", os.environ.get("STRIPE_PRICE_CONCIERGE_BUNDLE_MONTHLY", ""))

_PRICE_MAP = {
    "operator": PRICE_OPERATOR_MONTHLY,
    "growth": PRICE_GROWTH_MONTHLY,
    "scale": PRICE_SCALE_MONTHLY,
    "concierge_bundle": PRICE_CONCIERGE_BUNDLE,
    "beta": PRICE_BETA_MONTHLY,
    "beta_startup": PRICE_BETA_STARTUP,
}

# Legacy add-on price IDs
ADDON_PRICE_MAP = {
    "custom_brand_build": {
        "setup": os.environ.get("STRIPE_PRICE_CUSTOM_BRAND_BUILD_SETUP", ""),
        "monthly": os.environ.get("STRIPE_PRICE_CUSTOM_BRAND_BUILD_MONTHLY", ""),
    },
    "seo": {
        "setup": os.environ.get("STRIPE_PRICE_SEO_SETUP", ""),
        "monthly": os.environ.get("STRIPE_PRICE_SEO_MONTHLY", ""),
    },
    "email_setup": {
        "setup": os.environ.get("STRIPE_PRICE_EMAIL_SETUP", ""),
    },
    "curated_ad_plan": {
        "monthly": os.environ.get("STRIPE_PRICE_CURATED_AD_PLAN_MONTHLY", ""),
    },
}


def _ensure_configured():
    if not stripe.api_key:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")


def _price_id(plan: str) -> str:
    price = _PRICE_MAP.get(plan, "")
    if not price:
        # Fallback for legacy mapping
        if plan in ("beta", ""):
            price = PRICE_BETA_MONTHLY
        elif plan == "beta_startup":
            price = PRICE_BETA_STARTUP
    return price


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


def create_checkout_session(
    merchant_id,
    email,
    name,
    concierge_bundle=False,
    plan="operator",
    success_url=None,
    cancel_url=None,
):
    """Create a Stripe Checkout session for a Vantav tier + optional Concierge Bundle."""
    _ensure_configured()
    chosen_plan = plan.lower().strip()
    if chosen_plan in ("", "beta"):
        chosen_plan = "operator"

    price_id = _price_id(chosen_plan)
    if not price_id:
        raise RuntimeError(f"Stripe price ID for plan '{chosen_plan}' is not configured ({TIER_PRICE_ENV.get(chosen_plan, '')}).")

    selected_tier = PLAN_TO_TIER.get(chosen_plan, "Vantav Operator")

    line_items = [{"price": price_id, "quantity": 1}]

    add_ons = []
    subscription_metadata = {
        "merchant_id": merchant_id,
        "selected_tier": selected_tier,
        "concierge_bundle": "false",
    }
    if concierge_bundle:
        concierge_price = _price_id("concierge_bundle")
        if not concierge_price:
            raise RuntimeError("STRIPE_PRICE_CONCIERGE_BUNDLE is not configured")
        # One-time add-on fee charged on the first invoice alongside the subscription.
        subscription_metadata["concierge_bundle"] = "true"
        add_ons.append("concierge_bundle")
        line_items.append({"price": concierge_price, "quantity": 1})

    # Add concierge flag to success URL so the dashboard can prompt for Brand Build.
    default_success = "https://vantavcommerce.com/dashboard/billing?checkout=success"
    if concierge_bundle:
        default_success = _add_qs_param(default_success, "concierge_bundle", "true")
    if success_url:
        success_url = _add_qs_param(success_url, "concierge_bundle", "true") if concierge_bundle else success_url
    else:
        success_url = default_success

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
    # Don't set current_plan/add_ons here; those are applied after successful payment.
    db.session.commit()

    subscription_data = {"metadata": subscription_metadata}

    params = {
        "customer": customer_id,
        "mode": "subscription",
        "managed_payments": {"enabled": False},
        "line_items": line_items,
        "metadata": {
            "merchant_id": merchant_id,
            "selected_tier": selected_tier,
            "plan_choice": chosen_plan,
            "concierge_bundle": str(bool(concierge_bundle)).lower(),
        },
        "subscription_data": subscription_data,
        "allow_promotion_codes": True,
        "success_url": success_url,
        "cancel_url": cancel_url or "https://vantavcommerce.com/subscribe?canceled=1",
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


def verify_checkout_session(session_id: str) -> Dict[str, Any]:
    """Retrieve a Checkout session from Stripe and return key details."""
    _ensure_configured()
    if not session_id:
        raise ValueError("session_id is required")
    session = stripe.checkout.Session.retrieve(session_id)
    return {
        "id": session.id,
        "payment_status": session.get("payment_status"),
        "customer": session.get("customer"),
        "metadata": session.get("metadata", {}),
        "subscription": session.get("subscription"),
    }


def get_stripe_balance() -> Dict[str, Any]:
    """Return Stripe account balance with available and pending amounts."""
    _ensure_configured()
    try:
        bal = stripe.Balance.retrieve()
        available = [
            {"amount": b.amount, "currency": b.currency, "amount_decimal": b.amount / 100}
            for b in bal.available
        ]
        pending = [
            {"amount": b.amount, "currency": b.currency, "amount_decimal": b.amount / 100}
            for b in bal.pending
        ]
        return {
            "status": "ok",
            "available": available,
            "pending": pending,
            "currency": available[0]["currency"] if available else None,
        }
    except Exception as e:
        logger.error(f"[Stripe Balance] {e}")
        return {"status": "error", "error": str(e), "available": [], "pending": []}


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
