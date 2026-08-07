import pytest
import hmac
import hashlib
import json
import base64
from datetime import datetime
from app import app, SHOPIFY_WEBHOOK_SECRET


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
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


def test_magic_link_onboarding(client):
    from models import MagicLoginToken, MerchantProfile, db
    email = "magic-test-01@example.com"
    r = client.post("/api/v1/tenant/generate-magic-link", json={
        "admin_email": email,
        "business_name": "Magic Test Store",
        "selected_tier": "Pro Tier",
    })
    assert r.status_code == 201
    data = r.get_json()
    assert data["success"] is True
    assert "token=" in data["debug_link"]

    token = data["debug_link"].split("token=")[1]
    mlink = MagicLoginToken.query.get(token)
    assert mlink is not None
    assert mlink.admin_email == email

    # Login via magic link (follows redirect to /)
    r2 = client.get(f"/api/v1/auth/magic-login?token={token}", follow_redirects=True)
    assert r2.status_code == 200
    assert MagicLoginToken.query.get(token).is_used == 1

    # Cleanup
    profile = MerchantProfile.query.filter_by(admin_email=email).first()
    if profile:
        db.session.delete(profile)
    db.session.delete(mlink)
    db.session.commit()


def test_executive_digest_generation(client):
    from models import MerchantProfile, ActiveSession, db
    # Seed a session for the default merchant
    profile = MerchantProfile.query.get("merchant_shawn_01")
    if not profile:
        profile = MerchantProfile(merchant_id="merchant_shawn_01", business_name="Shawnzyluxe", admin_email="shawn@example.com")
        db.session.add(profile)
        db.session.commit()
    session_token = "test-session-digest-001"
    db.session.add(ActiveSession(token=session_token, merchant_id="merchant_shawn_01", created_at=datetime.utcnow()))
    db.session.commit()

    client.set_cookie("aegis_session_token", session_token)
    r = client.post("/api/v1/tenant/compile-executive-digest")
    assert r.status_code == 201
    data = r.get_json()
    assert data["success"] is True
    assert data["download_endpoint"] == "/api/v1/tenant/download-digest"

    r2 = client.get("/api/v1/tenant/download-digest")
    assert r2.status_code == 200
    assert "shawnzyluxe_executive_digest.html" in r2.headers.get("Content-Disposition", "")

    db.session.delete(ActiveSession.query.get(session_token))
    db.session.commit()
