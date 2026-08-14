#!/usr/bin/env python3
"""Simulate a Stripe checkout.session.completed webhook locally.

Usage:
    export STRIPE_WEBHOOK_SECRET=whsec_test_secret
    python scripts/simulate_stripe_webhook.py [BASE_URL] [MERCHANT_ID] [TIER]

Example:
    python scripts/simulate_stripe_webhook.py http://localhost:5000 merchant_shawn_01 "Vantav Growth"
"""

import hmac
import hashlib
import json
import os
import sys
import time
import uuid

import requests


def sign_payload(payload: str, secret: str) -> str:
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.{payload}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={sig}"


def main():
    base_url = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BASE_URL", "http://localhost:5000")).rstrip("/")
    merchant_id = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("MERCHANT_ID", "merchant_shawn_01")
    tier = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("SELECTED_TIER", "Vantav Growth")
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "whsec_test_secret")

    payload = json.dumps({
        "id": f"evt_{uuid.uuid4().hex[:24]}",
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": f"cs_{uuid.uuid4().hex[:24]}",
                "object": "checkout.session",
                "customer": f"cus_{uuid.uuid4().hex[:24]}",
                "subscription": f"sub_{uuid.uuid4().hex[:24]}",
                "metadata": {
                    "merchant_id": merchant_id,
                    "selected_tier": tier,
                    "concierge_bundle": "false",
                },
                "status": "complete",
            }
        },
    })

    headers = {
        "Stripe-Signature": sign_payload(payload, secret),
        "Content-Type": "application/json",
    }

    resp = requests.post(f"{base_url}/api/v1/webhooks/stripe-billing", data=payload, headers=headers)
    print(f"[{resp.status_code}] {resp.text}")
    resp.raise_for_status()


if __name__ == "__main__":
    main()
