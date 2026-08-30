import pytest

import welcome_pack
from app import app
from models import MerchantProfile


@pytest.fixture
def ctx():
    app.config["TESTING"] = True
    with app.app_context():
        yield


def _profile(tier="Basic Tier"):
    return MerchantProfile(
        merchant_id="tenant_test01",
        business_name="Test Storefront",
        admin_email="owner@example.com",
        account_tier=tier,
    )


def test_legacy_tiers_map_onto_a_package():
    assert welcome_pack.canonical_package_tier("Pro Tier") == "Vantav Growth"
    assert welcome_pack.canonical_package_tier("Beta + Startup Pack") == "Vantav Growth"
    assert welcome_pack.canonical_package_tier("unknown") == "Basic Tier"
    assert welcome_pack.canonical_package_tier("Vantav Growth") == "Vantav Growth"
    assert welcome_pack.canonical_package_tier(None) == "Basic Tier"


def test_package_limits_match_tier_manager():
    growth = welcome_pack.get_package("Vantav Growth")
    assert growth["limits"]["Connected stores"] == "5"
    assert growth["limits"]["Users included"] == "10"
    assert growth["limits"]["Support"] == "Priority"
    assert growth["amount_usd"] == 399.0


def test_free_registration_email_is_customer_facing(ctx):
    subject, html, text = welcome_pack.build_email(_profile())
    assert "Welcome to Vantav" in subject
    assert "No payment due" in html
    assert "$0.00" in html
    assert "How Vantav works" in html
    assert "Test Storefront" in text
    for internal in ("sandbox", "webhook", "merchant profile", "feature flag", "RLS"):
        assert internal.lower() not in html.lower()


def test_paid_email_shows_invoice_total_and_concierge(ctx):
    subject, html, _ = welcome_pack.build_email(
        _profile("Vantav Scale"),
        tier="Vantav Scale",
        amount_cents=179800,
        paid=True,
        concierge_bundle=True,
    )
    assert "invoice" in subject.lower()
    assert "Vantav Scale" in html
    assert "Concierge Bundle" in html
    assert "$1,798.00" in html
    assert "Paid" in html


def test_send_is_safe_when_delivery_fails(ctx, monkeypatch):
    monkeypatch.setattr(welcome_pack.outbound, "send_transactional_email",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("mailgun down")))
    assert welcome_pack.send_welcome_package("tenant_missing")["status"] in ("failed", "skipped")


def test_send_uses_merchant_email(ctx, monkeypatch):
    from models import db
    sent = {}

    def fake_send(to, subject, html, text_body=None):
        sent.update({"to": to, "subject": subject})
        return True

    monkeypatch.setattr(welcome_pack.outbound, "send_transactional_email", fake_send)
    profile = MerchantProfile(
        merchant_id="tenant_wp_send",
        business_name="Send Test",
        admin_email="send-test@example.com",
        account_tier="Vantav Operator",
    )
    db.session.add(profile)
    db.session.commit()
    try:
        result = welcome_pack.send_welcome_package("tenant_wp_send", tier="Vantav Operator")
        assert result["status"] == "ok"
        assert sent["to"] == "send-test@example.com"
        assert "Vantav Operator" in sent["subject"]
    finally:
        db.session.delete(profile)
        db.session.commit()
