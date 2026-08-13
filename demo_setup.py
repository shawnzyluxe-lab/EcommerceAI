"""Seed the local SQLite DB with a demo merchant + matching profit feed."""
import os
from datetime import datetime, timezone
from decimal import Decimal

from werkzeug.security import generate_password_hash

# Ensure local dev settings for the demo.
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

from app import app
from models import db, MerchantProfile, ProfitFeedOrder
from profit_feed import CHANNEL_DEFAULTS, _normalize_channel
import channels as channels_module
import alert_matrix
import sandbox_demo

MERCHANT_ID = os.environ.get("DEMO_MERCHANT_ID", "merchant_ivor_demo")
EMAIL = os.environ.get("DEMO_MERCHANT_EMAIL", "ivonderhaff@gmail.com")
PASSWORD = os.environ.get("DEMO_MERCHANT_PASSWORD", "Pqk57Qa9Weo")
BUSINESS_NAME = os.environ.get("DEMO_BUSINESS_NAME") or (EMAIL.split("@")[0].replace(".", " ").title() if "@" in EMAIL else "Your Store")

STATIC_ORDERS = [
    {"id": "#1042", "channel": "Shopify",     "items": 2, "revenue": 128.00, "profit": 38.42, "state": "delayed"},
    {"id": "#1041", "channel": "TikTok Shop", "items": 1, "revenue": 64.00,  "profit": 11.90, "state": "shipped"},
    {"id": "#1040", "channel": "Shopify",     "items": 3, "revenue": 214.50, "profit": 79.10, "state": "shipped"},
    {"id": "#1039", "channel": "Amazon",      "items": 1, "revenue": 82.00,  "profit": 14.05, "state": "packed"},
    {"id": "#1038", "channel": "Etsy",        "items": 2, "revenue": 96.40,  "profit": 27.60, "state": "delayed"},
    {"id": "#1037", "channel": "Shopify",     "items": 1, "revenue": 58.00,  "profit": -3.20, "state": "refunded"},
]


def main():
    with app.app_context():
        db.create_all()

        profile = MerchantProfile.query.get(MERCHANT_ID)
        if not profile:
            profile = MerchantProfile(merchant_id=MERCHANT_ID)
        profile.business_name = BUSINESS_NAME
        profile.admin_email = EMAIL
        profile.password_hash = generate_password_hash(PASSWORD, method="pbkdf2:sha256")
        profile.account_tier = "Enterprise AI Tier"
        profile.sandbox_status = "approved"
        profile.live_access_enabled = 1
        db.session.add(profile)
        db.session.commit()

        # Clear old demo data for this merchant so we can re-seed cleanly.
        ProfitFeedOrder.query.filter_by(merchant_id=MERCHANT_ID).delete()
        db.session.commit()

        total_revenue_target = Decimal("4582.00")
        total_profit_target = Decimal("1394.00")

        total_rev = sum(Decimal(str(o["revenue"])) for o in STATIC_ORDERS)
        total_prof = sum(Decimal(str(o["profit"])) for o in STATIC_ORDERS)

        scale_rev = total_revenue_target / total_rev
        scale_prof = total_profit_target / total_prof

        for o in STATIC_ORDERS:
            channel = _normalize_channel(o["channel"])
            defaults = CHANNEL_DEFAULTS.get(channel, CHANNEL_DEFAULTS["shopify"])
            gross = float(Decimal(str(o["revenue"])) * scale_rev)
            net = float(Decimal(str(o["profit"])) * scale_prof)
            items = max(int(o["items"]), 1)
            fees = gross * defaults["fee_pct"] + defaults["fee_fixed"]
            shipping = defaults["shipping"] * items
            cogs = gross - net - fees - shipping
            if cogs < 0:
                cogs = gross - net
                fees = 0.0
                shipping = 0.0

            order = ProfitFeedOrder(
                merchant_id=MERCHANT_ID,
                order_id=o["id"],
                channel=channel,
                items=items,
                gross_revenue=gross,
                marketplace_fees=round(fees, 2),
                cost_of_goods_sold=round(cogs, 2),
                shipping_costs=round(shipping, 2),
                ad_spend_attributed=0.0,
                refund_amount=0.0,
                net_profit=round(net, 2),
                state=o["state"],
                recorded_at=datetime.now(timezone.utc),
            )
            db.session.add(order)

        db.session.commit()

        # Seed demo channels and alerts for the merchant.
        sandbox_demo._seed_demo_channels(MERCHANT_ID)
        alert_matrix.seed_demo_alerts(MERCHANT_ID)

        # Verify seeded totals.
        from profit_feed import get_profit_breakdown
        bd = get_profit_breakdown(MERCHANT_ID)
        print(f"Seeded merchant {MERCHANT_ID}: gross={bd['gross_revenue']}, net={bd['net_profit']}")


if __name__ == "__main__":
    main()
