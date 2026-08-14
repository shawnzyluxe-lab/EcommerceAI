#!/usr/bin/env python3
"""Simulate a TikTok Shop order webhook locally.

Usage:
    python scripts/test_tiktok_order_webhook.py [BASE_URL] [MERCHANT_ID]

Example:
    python scripts/test_tiktok_order_webhook.py http://localhost:5000 merchant_shawn_01
"""

import os
import sys
import uuid

import requests


def main():
    base_url = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BASE_URL", "http://localhost:5000")).rstrip("/")
    merchant_id = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("MERCHANT_ID", "merchant_shawn_01")
    event_id = f"tiktok-{uuid.uuid4().hex[:16]}"

    payload = {
        "type": "ORDER_STATUS_CHANGE",
        "order_id": f"{uuid.uuid4().hex[:12].upper()}",
        "create_time": 1786582400,
        "order_amount": 2500.00,
        "total_amount": 2500.00,
        "original_total_amount": 2500.00,
        "shipping_fee": 400.00,
        "tax_amount": 150.00,
        "status": "AWAITING_SHIPMENT",
        "line_items": [
            {
                "sku": "SKU-TRACK-JACKET",
                "sku_id": "SKU-TRACK-JACKET",
                "product_id": "prod_tiktok_001",
                "product_name": "Oversized Utility Cyber Jacket",
                "quantity": 2,
                "sale_price": {"amount": 1250.00, "currency": "USD"},
            }
        ],
        "recipient_address": {
            "phone": "305-555-0199",
            "city": "Miami",
            "state": "FL",
        },
    }

    headers = {
        "Content-Type": "application/json",
        "X-Tiktok-Event-Id": event_id,
        "Authorization": "mock_verified_secure_hex_hash",
    }

    resp = requests.post(
        f"{base_url}/api/v1/webhooks/tiktok-orders?merchant_id={merchant_id}",
        json=payload,
        headers=headers,
    )
    print(f"[{resp.status_code}] {resp.text}")
    resp.raise_for_status()


if __name__ == "__main__":
    main()
