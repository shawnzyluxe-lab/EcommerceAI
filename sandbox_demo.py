"""Demo data seeding for sandbox merchants."""
from models import db, BusinessMetric, StartupPackProject
import profit_feed
import alert_matrix
import action_gate
import channels as channels_module
import startup_pack

DEMO_ORDERS = [
    ("shopify", 2, 128.00, "shipped"),
    ("tiktok", 1, 64.00, "shipped"),
    ("amazon", 1, 82.00, "packed"),
    ("shopify", 3, 214.50, "shipped"),
    ("etsy", 2, 96.40, "delayed"),
    ("shopify", 1, 58.00, "refunded"),
    ("tiktok", 2, 132.00, "shipped"),
    ("amazon", 1, 49.99, "shipped"),
    ("shopify", 1, 89.00, "delayed"),
]

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
    )
    channels_module.connect_amazon(
        merchant_id,
        f"demo_seller_{suffix}",
        "demo_access_key",
        "demo_secret_key",
        "us-east-1",
    )


def _seed_demo_startup_pack(merchant_id, business_name):
    """Pre-fill the Brand Build project with demo-ready sample data."""
    project = startup_pack.get_project(merchant_id)
    brand = business_name if business_name and business_name != "New Storefront" else "Luxe Sleep Co."
    project.brand_name = brand
    project.niche = "Premium Home & Sleep"
    project.target_audience = "Gen Z women 22-34, US, interest in wellness and self-care"
    project.monthly_ad_budget = 2500.0
    project.design_vibe = "Minimal / Calm / Premium"
    project.has_domain = True
    project.sample_product = "Satin Sleep Set (Ivory)"
    project.status = "pending_brief"
    project.next_steps = "Curated supplier matches and sample order instructions are being prepared."
    project.checklist = startup_pack._default_checklist(brand, project.niche)
    db.session.commit()


def seed_sandbox_demo(merchant_id, business_name=""):
    """Populate a sandbox merchant with realistic demo data.

    Safe to call multiple times: returns False if data already exists.
    """
    from models import ProfitFeedOrder

    if ProfitFeedOrder.query.filter_by(merchant_id=merchant_id).first():
        return False

    suffix = _order_suffix(merchant_id)
    for i, (channel, items, gross, state) in enumerate(DEMO_ORDERS):
        order_id = f"DEMO-{suffix}-{i + 1001}"
        profit_feed.record_order(merchant_id, channel, order_id, gross, items=items, state=state)

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

    return True
