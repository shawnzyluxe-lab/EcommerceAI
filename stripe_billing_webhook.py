"""Stripe billing webhook helpers for Vantav.

Provides HMAC signature verification, tier seat provisioning, and event
parsing. Can be consumed by the Flask app or mounted as a FastAPI sub-module.
"""

import hashlib
import hmac
import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# Map plan tier keys to Vantav tier names and seat limits.
TIER_LIMITS_CONFIG: Dict[str, Dict[str, Any]] = {
    "starter_core": {"vantav_tier": "Basic Tier", "max_seats": 1, "features_tier": "core"},
    "copilot_pro": {"vantav_tier": "Vantav Growth", "max_seats": 10, "features_tier": "pro"},
    "autonomous_scale": {"vantav_tier": "Vantav Scale", "max_seats": 25, "features_tier": "scale"},
    # Direct Vantav tier keys are also accepted.
    "vantav_operator": {"vantav_tier": "Vantav Operator", "max_seats": 3, "features_tier": "core"},
    "vantav_growth": {"vantav_tier": "Vantav Growth", "max_seats": 10, "features_tier": "pro"},
    "vantav_scale": {"vantav_tier": "Vantav Scale", "max_seats": 25, "features_tier": "scale"},
}


def verify_stripe_signature(raw_payload: bytes, sig_header: str, secret: Optional[str] = None) -> bool:
    """Verify a Stripe webhook signature using HMAC-SHA256.

    Expects the Stripe-Signature header in the form:
        t=<timestamp>,v1=<signature>
    """
    secret = secret or STRIPE_WEBHOOK_SECRET
    if not secret or not sig_header:
        logger.warning("Stripe webhook secret or signature header missing.")
        return False

    try:
        sig_parts = dict(x.split("=") for x in sig_header.split(","))
        timestamp = sig_parts.get("t", "")
        stripe_hash = sig_parts.get("v1", "")
    except Exception:
        logger.warning("Invalid Stripe signature format.")
        return False

    signed_payload = f"{timestamp}.{raw_payload.decode('utf-8')}".encode("utf-8")
    computed_hash = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed_hash, stripe_hash)


def parse_tier_from_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract tier and seat provisioning from a Stripe subscription event."""
    event_type = event.get("type")
    if event_type not in ("customer.subscription.created", "customer.subscription.updated"):
        return None

    subscription_obj = event.get("data", {}).get("object", {})
    stripe_customer_id = subscription_obj.get("customer")
    stripe_subscription_id = subscription_obj.get("id")
    metadata = subscription_obj.get("metadata", {})
    plan_tier_key = metadata.get("plan_tier_key") or metadata.get("selected_tier", "starter_core")
    status = subscription_obj.get("status")

    if status != "active":
        return {
            "stripe_customer_id": stripe_customer_id,
            "stripe_subscription_id": stripe_subscription_id,
            "max_seats": 0,
            "assigned_tier": "locked",
            "vantav_tier": "Basic Tier",
            "status": status,
        }

    tier_profile = TIER_LIMITS_CONFIG.get(str(plan_tier_key).lower(), TIER_LIMITS_CONFIG["starter_core"])
    return {
        "stripe_customer_id": stripe_customer_id,
        "stripe_subscription_id": stripe_subscription_id,
        "max_seats": tier_profile["max_seats"],
        "assigned_tier": tier_profile["features_tier"],
        "vantav_tier": tier_profile["vantav_tier"],
        "status": status,
    }


def process_stripe_event(raw_payload: bytes, sig_header: str, secret: Optional[str] = None) -> Dict[str, Any]:
    """Verify signature and parse the event. Returns a provisioning payload or raises ValueError."""
    if not verify_stripe_signature(raw_payload, sig_header, secret):
        raise ValueError("Unauthorized billing event transmission.")

    event = json.loads(raw_payload.decode("utf-8"))
    result = parse_tier_from_event(event)
    if result:
        return result
    return {"status": "ignored_event_type", "type": event.get("type")}
