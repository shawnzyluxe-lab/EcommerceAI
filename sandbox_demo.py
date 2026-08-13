"""Demo data seeding for sandbox merchants."""
import json
from models import (
    db,
    BusinessMetric,
    StartupPackProject,
    MerchantProfile,
    ProfitFeedOrder,
    AdSpendFeed,
    Alert,
    PendingAction,
    MerchantChannel,
    TenantOAuthToken,
    Product,
    Supplier,
    UnifiedOrder,
)
import profit_feed
import alert_matrix
import action_gate
import channels as channels_module
import startup_pack
import unified_ingest

DEMO_ORDERS = [
    ("shopify", 2, 128.00, "shipped", "1Z999AA10123456784", "ups"),
    ("tiktok", 1, 64.00, "shipped", "9400111899223456789012", "usps"),
    ("amazon", 1, 82.00, "packed", "TBA123456789012", "amazon"),
    ("shopify", 3, 214.50, "shipped", "449044304137821", "fedex"),
    ("etsy", 2, 96.40, "delayed", "LX1234567890", "lasership"),
    ("shopify", 1, 58.00, "refunded", "", ""),
    ("tiktok", 2, 132.00, "shipped", "JJD0001123456789", "dhl"),
    ("amazon", 1, 49.99, "shipped", "TBC987654321098", "amazon"),
    ("shopify", 1, 89.00, "delayed", "C12345678901234", "ontrac"),
]

DEMO_ORDER_SKUS = [
    "SZL-VAR-A",
    "SZL-VAR-B",
    "SZL-VAR-C",
    "SZL-VAR-A",
    "SZL-VAR-D",
    "SZL-VAR-A",
    "SZL-VAR-B",
    "SZL-VAR-C",
    "SZL-VAR-A",
]

DEMO_PRODUCTS = {
    "SZL-VAR-A": {"title": "Satin Sleep Set", "on_hand": 68, "unit_cost": 14.0},
    "SZL-VAR-B": {"title": "Velvet Scrunchie Pack", "on_hand": 30, "unit_cost": 5.0},
    "SZL-VAR-C": {"title": "Silk Pillowcase", "on_hand": 120, "unit_cost": 9.0},
    "SZL-VAR-D": {"title": "Satin Robe", "on_hand": 40, "unit_cost": 18.0},
}

DEMO_AD_SPEND = [
    ("meta", 80.00, 24),
    ("tiktok", 120.00, 38),
    ("amazon", 40.00, 7),
]


def _order_suffix(merchant_id):
    """Return a short merchant suffix that is safe for order IDs."""
    return merchant_id.replace("_", "-").replace("tenant-", "")[-8:]


def _seed_demo_channels(merchant_id):
    """Connect simulated demo channels for the sandbox merchant."""
    from models import MerchantChannel

    suffix = _order_suffix(merchant_id)
    channels_module.connect_shopify(
        merchant_id,
        f"demo-{suffix}.myshopify.com",
        "demo_sandbox_token",
    )
    channels_module.connect_tiktok(
        merchant_id,
        f"demo_{suffix}",
        "demo_app_key",
        "demo_app_secret",
        access_token="demo_access_token",
        shop_cipher="",
    )
    channels_module.connect_amazon(
        merchant_id,
        f"demo_seller_{suffix}",
        "demo_access_key",
        "demo_secret_key",
        "us-east-1",
        refresh_token="demo_refresh_token",
        lwa_client_id="demo_lwa_id",
        lwa_client_secret="demo_lwa_secret",
        role_arn="",
    )

    # Set realistic demo pending-order counts on each channel.
    demo_orders = {"shopify": 12, "tiktok": 7, "amazon": 4}
    for platform, count in demo_orders.items():
        mc = MerchantChannel.query.filter_by(merchant_id=merchant_id, channel_id=platform).first()
        if mc:
            mc.pending_orders = count
            mc.conversion_rate = 3.5
    db.session.commit()


def _seed_demo_startup_pack(merchant_id, business_name):
    """Pre-fill the Brand Build project with demo-ready sample data."""
    project = startup_pack.get_project(merchant_id)
    brand = "Luxe Sleep Co."
    project.brand_name = brand
    project.niche = "Premium Home & Sleep"
    project.target_audience = "Gen Z women 22-34, US, interest in wellness and self-care"
    project.monthly_ad_budget = 2500.0
    project.design_vibe = "Minimal / Calm / Premium"
    project.has_domain = True
    project.sample_product = "Satin Sleep Set (Ivory)"
    project.status = "pending_brief"
    project.next_steps = "Curated supplier matches and sample order instructions are being prepared."
    project.checklist = json.dumps(startup_pack._default_checklist(brand, project.niche))
    db.session.commit()


def _clear_demo_data(merchant_id):
    """Remove any previously seeded demo data for this merchant."""
    PendingAction.query.filter_by(merchant_id=merchant_id).delete()
    ProfitFeedOrder.query.filter_by(merchant_id=merchant_id).delete()
    UnifiedOrder.query.filter_by(merchant_id=merchant_id).delete()
    AdSpendFeed.query.filter_by(merchant_id=merchant_id).delete()
    Alert.query.filter_by(merchant_id=merchant_id).delete()
    MerchantChannel.query.filter_by(merchant_id=merchant_id).delete()
    TenantOAuthToken.query.filter_by(merchant_id=merchant_id).delete()
    StartupPackProject.query.filter_by(merchant_id=merchant_id).delete()
    Product.query.filter_by(merchant_id=merchant_id).delete()
    Supplier.query.filter_by(merchant_id=merchant_id).delete()
    db.session.commit()


def seed_sandbox_demo(merchant_id, business_name="", force=False):
    """Populate a sandbox merchant with realistic demo data.

    Safe to call multiple times: returns False if data already exists unless force=True.
    """
    if ProfitFeedOrder.query.filter_by(merchant_id=merchant_id).first():
        if not force:
            return False
        _clear_demo_data(merchant_id)

    # Seed unified supplier/product catalog for forecasting.
    supplier = unified_ingest.upsert_supplier(merchant_id, name="Demo Supplier", lead_days=10)
    for sku, data in DEMO_PRODUCTS.items():
        unified_ingest.upsert_product(
            merchant_id=merchant_id,
            sku=sku,
            title=data["title"],
            unit_cost=data["unit_cost"],
            on_hand=data["on_hand"],
            supplier_id=supplier.id,
        )

    suffix = _order_suffix(merchant_id)
    for i, (channel, items, gross, state, tracking_number, carrier) in enumerate(DEMO_ORDERS):
        order_id = f"DEMO-{suffix}-{i + 1001}"
        sku = DEMO_ORDER_SKUS[i]
        unit_price = round(gross / max(items, 1), 4)
        unit_cost = DEMO_PRODUCTS.get(sku, {}).get("unit_cost", unit_price * 0.35)
        order_items = [{
            "sku": sku,
            "qty": items,
            "price": unit_price,
            "unit_cost": unit_cost,
            "title": DEMO_PRODUCTS.get(sku, {}).get("title", sku),
        }]
        profit_feed.record_order(
            merchant_id,
            channel,
            order_id,
            gross,
            items=items,
            state=state,
            tracking_number=tracking_number,
            carrier=carrier,
            order_items=order_items,
        )

    for platform, amount, conv in DEMO_AD_SPEND:
        profit_feed.record_ad_spend(merchant_id, platform, amount, conv)

    alert_matrix.seed_demo_alerts(merchant_id)
    action_gate.refresh_actions(merchant_id)
    _seed_demo_channels(merchant_id)
    _seed_demo_startup_pack(merchant_id, business_name)

    bm = BusinessMetric.query.filter_by(merchant_id=merchant_id).first()
    if bm:
        bm.ai_briefing = "Demo mode: profit, alerts, and actions are generated from simulated multi-channel data."
        db.session.commit()

    # Preserve the merchant's chosen business name; only fill an empty one.
    profile = MerchantProfile.query.filter_by(merchant_id=merchant_id).first()
    if profile and not profile.business_name:
        default_name = business_name or (profile.admin_email.split("@")[0] if profile.admin_email and "@" in profile.admin_email else "Your Store")
        profile.business_name = default_name
        db.session.commit()

    return True
