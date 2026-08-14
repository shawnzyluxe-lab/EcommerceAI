"""Vantav multi-channel webhook sync diagnostic suite.

Validates Shopify HMAC verification and concurrent webhook ingestion against the
local Flask app running on port 8000. Falls back to starting the dev server if no
service is detected.
"""

import asyncio
import atexit
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import uuid
from typing import Tuple

import requests

SHOPIFY_WEBHOOK_SECRET = "hush_shopify_secret_key_101"
AMAZON_WEBHOOK_SECRET = "hush_amazon_secret_key_101"
INGEST_URL_SHOPIFY = "http://localhost:8000/api/v1/webhooks/shopify-orders?merchant_id=merchant_shawn_01"
INGEST_URL_AMAZON = "http://localhost:8000/api/v1/webhooks/amazon-orders?merchant_id=merchant_shawn_01"

_server_proc = None


def _ensure_server():
    """Start the Flask dev server with webhook secrets enabled if it is not already running."""
    global _server_proc
    try:
        r = requests.get("http://localhost:8000/health", timeout=2)
        if r.status_code == 200:
            print("[+] Local service already running on port 8000")
            return
    except Exception:
        pass

    root = os.path.dirname(os.path.abspath(__file__))

    # Remove stale SQLite test databases so the schema is created fresh.
    if not os.environ.get("DATABASE_URL"):
        stale_dbs = [
            os.path.join(root, "shawnzyluxe.db"),
            os.path.join(root, "instance", "shawnzyluxe.db"),
            os.path.join(root, ".local_test.db"),
        ]
        for db_path in stale_dbs:
            if os.path.exists(db_path):
                os.remove(db_path)
                print(f"[+] Removed stale test DB: {db_path}")
        os.makedirs(os.path.join(root, "instance"), exist_ok=True)

    print("[+] Starting Flask dev server for diagnostics...")
    env = os.environ.copy()
    env.update({
        "FLASK_APP": "app.py",
        "SHOPIFY_WEBHOOK_SECRET": SHOPIFY_WEBHOOK_SECRET,
        "AMAZON_WEBHOOK_SECRET": AMAZON_WEBHOOK_SECRET,
    })
    _server_proc = subprocess.Popen(
        [sys.executable, "-m", "flask", "run", "--host=127.0.0.1", "--port=8000"],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    for _ in range(30):
        try:
            if requests.get("http://localhost:8000/health", timeout=2).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        _shutdown_server()
        raise RuntimeError("Flask dev server failed to become healthy")

    # Seed a test merchant with baseline data.
    seed_path = os.path.join(root, "scripts", "seed_local_merchant.py")
    if os.path.exists(seed_path):
        subprocess.run([sys.executable, seed_path], cwd=root, env=env, check=False)


def _shutdown_server():
    global _server_proc
    if _server_proc and _server_proc.poll() is None:
        _server_proc.terminate()
        try:
            _server_proc.wait(timeout=5)
        except Exception:
            _server_proc.kill()


atexit.register(_shutdown_server)


class VantavSyncDiagnosticSuite:
    @staticmethod
    def construct_shopify_signed_payload(payload_dict: dict) -> Tuple[str, str, str]:
        """Generate a raw JSON body and a valid Base64 HMAC-SHA256 signature."""
        raw_body = json.dumps(payload_dict)
        computed = hmac.new(
            SHOPIFY_WEBHOOK_SECRET.encode("utf-8"),
            raw_body.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return raw_body, base64.b64encode(computed).decode("utf-8")

    @classmethod
    def test_shopify_ingest_pipeline(cls) -> bool:
        print("\n[*] SEC_TEST: Validating Shopify cryptographic perimeter signature verification...")

        order_id = f"diag-shopify-{uuid.uuid4().hex[:8]}"
        mock_order = {
            "id": 883291,
            "name": order_id,
            "total_price": "250.00",
            "currency": "USD",
            "line_items": [
                {"sku": "SKU-TRACK-JACKET", "quantity": 1, "price": "250.00", "title": "Track Jacket"}
            ],
        }
        raw_body, valid_sig = cls.construct_shopify_signed_payload(mock_order)

        # Malicious payload: valid base64 but wrong signature
        bad_sig = base64.b64encode(b"wrong-signature-bytes").decode("utf-8")
        bad_headers = {
            "X-Shopify-Hmac-SHA256": bad_sig,
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Webhook-Id": f"{order_id}-bad",
            "Content-Type": "application/json",
        }
        res_malicious = requests.post(INGEST_URL_SHOPIFY, data=raw_body, headers=bad_headers)

        # Valid payload
        good_headers = {
            "X-Shopify-Hmac-SHA256": valid_sig,
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Webhook-Id": f"{order_id}-good",
            "Content-Type": "application/json",
        }
        res_valid = requests.post(INGEST_URL_SHOPIFY, data=raw_body, headers=good_headers)

        if res_malicious.status_code == 401 and res_valid.status_code == 200:
            print("[PASS] Shopify security gateway confirmed. Unauthorized updates blocked.")
            return True

        print(f"[FAIL] Perimeter variation. Valid: {res_valid.status_code}, Malicious: {res_malicious.status_code}")
        print(f"       Valid response: {res_valid.text[:200]}")
        return False

    @classmethod
    def test_amazon_ingest_pipeline(cls) -> bool:
        print("\n[*] INTEGRITY_TEST: Validating Amazon order webhook ingestion...")

        event_id = f"diag-amazon-{uuid.uuid4().hex[:8]}"
        payload = {
            "AmazonOrderId": event_id,
            "AmazonOrderTotal": "99.00",
            "NumberOfItemsShipped": 1,
            "OrderItems": [
                {"SellerSKU": "SKU-TRACK-JACKET", "Quantity": 1, "ItemPrice": {"Amount": "99.00"}, "Title": "Track Jacket"}
            ],
        }
        headers = {"X-Amazon-Sqs-Message-Id": event_id, "Content-Type": "application/json"}
        res = requests.post(INGEST_URL_AMAZON, json=payload, headers=headers)

        if res.status_code == 200:
            print(f"[PASS] Amazon webhook accepted. Response: {res.json()}")
            return True

        print(f"[FAIL] Amazon webhook rejected: {res.status_code} {res.text[:200]}")
        return False

    @classmethod
    async def simulate_race_condition_spikes(cls):
        print("\n[*] CONCURRENCY_TEST: Triggering 5 simultaneous Shopify webhook calls...")

        raw_body, valid_sig = cls.construct_shopify_signed_payload({
            "id": 110,
            "name": "diag-shopify-race-base",
            "total_price": "99.00",
            "currency": "USD",
            "line_items": [
                {"sku": "SKU-TRACK-JACKET", "quantity": 1, "price": "99.00", "title": "Track Jacket"}
            ],
        })

        async def post_webhook(worker_id: int):
            event_id = f"diag-shopify-race-{uuid.uuid4().hex[:8]}-{worker_id}"
            headers = {
                "X-Shopify-Hmac-SHA256": valid_sig,
                "X-Shopify-Topic": "orders/create",
                "X-Shopify-Webhook-Id": event_id,
                "Content-Type": "application/json",
            }
            start = time.perf_counter()
            loop = asyncio.get_event_loop()
            res = await loop.run_in_executor(
                None, lambda: requests.post(INGEST_URL_SHOPIFY, data=raw_body, headers=headers)
            )
            latency = (time.perf_counter() - start) * 1000
            print(f"  -> Worker [{worker_id}] Response: {res.status_code} | Latency: {latency:.1f}ms")
            return res.status_code

        tasks = [post_webhook(i) for i in range(5)]
        status_codes = await asyncio.gather(*tasks)

        if all(code == 200 for code in status_codes):
            print("[PASS] Concurrent webhook ingestion succeeded with no lock contention.")
        else:
            print("[WARNING] Webhook collision detected. Check database connection pools.")


if __name__ == "__main__":
    print("=====================================================================")
    print("VANTAV INTEGRATION WEBHOOK SYNCHRONIZATION DIAGNOSTIC")
    print("=====================================================================\n")

    _ensure_server()

    success = VantavSyncDiagnosticSuite.test_shopify_ingest_pipeline()
    if success:
        VantavSyncDiagnosticSuite.test_amazon_ingest_pipeline()
        asyncio.run(VantavSyncDiagnosticSuite.simulate_race_condition_spikes())

    print("\n=====================================================================")
    print("DIAGNOSTIC TRACE COMPLETE")
    print("=====================================================================")
