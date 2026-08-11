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
PRICE_STARTUP_ADDON = os.environ.get("STRIPE_PRICE_STARTUP_ADDON", "")


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


def create_checkout_session(merchant_id, email, name, include_startup_addon=False, success_url=None, cancel_url=None):
    """Create a Stripe Checkout session for the beta subscription + optional add-on."""
    _ensure_configured()
    if not PRICE_BETA_MONTHLY:
        raise RuntimeError("STRIPE_PRICE_BETA_MONTHLY is not configured")

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
    billing.current_plan = billing.current_plan or "Beta Tier"
    db.session.commit()

    line_items = [{"price": PRICE_BETA_MONTHLY, "quantity": 1}]
    add_invoice_items = []
    if include_startup_addon and PRICE_STARTUP_ADDON:
        add_invoice_items.append({"price": PRICE_STARTUP_ADDON})

    params = {
        "customer": customer_id,
        "mode": "subscription",
        "line_items": line_items,
        "metadata": {
            "merchant_id": merchant_id,
            "selected_tier": "Beta Tier",
            "startup_addon": str(include_startup_addon).lower(),
        },
        "subscription_data": {
            "metadata": {
                "merchant_id": merchant_id,
                "selected_tier": "Beta Tier",
            }
        },
        "allow_promotion_codes": True,
        "success_url": success_url or "https://vantavcommerce.com/dashboard/billing?checkout=success",
        "cancel_url": cancel_url or "https://vantavcommerce.com/subscribe?canceled=1",
        "automatic_tax": {"enabled": False},
    }
    if add_invoice_items:
        params["add_invoice_items"] = add_invoice_items

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
