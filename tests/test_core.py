import pytest
import hmac
import hashlib
import json
import base64
from app import app, SHOPIFY_WEBHOOK_SECRET


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as test_client:
        yield test_client


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "HEALTHY"
    assert data["database_connected"] is True


def test_webhook_idempotency(client):
    payload_data = {"total_price": "150.00"}
    encoded_payload = json.dumps(payload_data).encode("utf-8")

    signature = base64.b64encode(
        hmac.new(SHOPIFY_WEBHOOK_SECRET, encoded_payload, hashlib.sha256).digest()
    ).decode()

    headers = {
        "X-Shopify-Hmac-SHA256": signature,
        "X-Shopify-Webhook-Id": "test_evt_token_0001",
    }

    response_one = client.post(
        "/api/v1/webhooks/shopify-orders",
        data=encoded_payload,
        headers=headers,
    )
    assert response_one.status_code == 200

    response_two = client.post(
        "/api/v1/webhooks/shopify-orders",
        data=encoded_payload,
        headers=headers,
    )
    assert response_two.status_code == 200
    assert response_two.get_json()["status"] == "duplicate_ignored"
