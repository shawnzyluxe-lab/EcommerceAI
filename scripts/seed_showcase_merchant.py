"""Seed a full showcase merchant for marketing captures and walkthroughs.

Creates a merchant with 30 days of multi-channel orders, ad spend, inventory,
open alerts and pending actions so every dashboard page renders with realistic
data instead of empty states.

Usage:
    .venv/bin/python scripts/seed_showcase_merchant.py

Environment overrides: SHOWCASE_MERCHANT_ID, SHOWCASE_EMAIL, SHOWCASE_PASSWORD,
SHOWCASE_BUSINESS_NAME.
"""
import os
import random
import secrets
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

from werkzeug.security import generate_password_hash  # noqa: E402

from app import app  # noqa: E402
from models import (  # noqa: E402
    db,
    ActionEvidence,
    AdSpendFeed,
    Alert,
    MerchantProfile,
    PendingAction,
    Product,
    ProfitFeedOrder,
    UnifiedOrder,
)
from profit_feed import CHANNEL_DEFAULTS, _normalize_channel, get_profit_breakdown  # noqa: E402
import action_gate  # noqa: E402
import sandbox_demo  # noqa: E402

MERCHANT_ID = os.environ.get("SHOWCASE_MERCHANT_ID", "merchant_demo_video")
EMAIL = os.environ.get("SHOWCASE_EMAIL", "demo@vantavcommerce.com")
PASSWORD = os.environ.get("SHOWCASE_PASSWORD") or secrets.token_urlsafe(12)
BUSINESS_NAME = os.environ.get("SHOWCASE_BUSINESS_NAME", "Aurora Supply")

CATALOG = [
    # sku, title, channel, price, unit_cost, on_hand, reorder_point, weight
    ("AUR-SLK-001", "Satin Sleep Set", "shopify", 128.00, 67.50, 68, 90, 26),
    ("AUR-SLK-002", "Silk Pillowcase Pair", "shopify", 64.00, 30.50, 412, 120, 22),
    ("AUR-CND-010", "Amber Soy Candle", "tiktok", 34.00, 15.20, 780, 200, 18),
    ("AUR-ROB-004", "Waffle Lounge Robe", "amazon", 96.00, 55.60, 143, 80, 14),
    ("AUR-BAG-021", "Quilted Weekender", "shopify", 214.00, 117.00, 54, 40, 8),
    ("AUR-CND-011", "Cedar + Fig Diffuser", "tiktok", 48.00, 22.10, 96, 150, 12),
]

CHANNEL_MIX = {"shopify": 0.52, "tiktok": 0.28, "amazon": 0.20}
# Ad spend is attributed per channel across the recent order window at query
# time, so keep the daily totals proportional to a lean, profitable brand.
DAILY_AD_SPEND = {"tiktok": 18.0, "meta": 14.0, "amazon": 8.0}


def _clear(merchant_id):
    for model in (ProfitFeedOrder, AdSpendFeed, Alert, ActionEvidence, PendingAction, Product, UnifiedOrder):
        model.query.filter_by(merchant_id=merchant_id).delete()
    db.session.commit()


def _seed_products(merchant_id):
    for sku, title, channel, _price, unit_cost, on_hand, reorder_point, _w in CATALOG:
        db.session.add(Product(
            sku=sku,
            merchant_id=merchant_id,
            title=title,
            channel_ids=channel,
            on_hand=on_hand,
            inbound=0,
            reorder_point=reorder_point,
            unit_cost=unit_cost,
        ))
    db.session.commit()


def _seed_orders(merchant_id, days=30):
    rng = random.Random(20260830)
    now = datetime.now(timezone.utc)
    order_no = 4100
    weights = [w for _s, _t, _c, _p, _u, _o, _r, w in CATALOG]

    for day_offset in range(days, -1, -1):
        day = now - timedelta(days=day_offset)
        # Gentle growth curve plus weekend lift.
        base = 5 + int((days - day_offset) * 0.18)
        if day.weekday() >= 5:
            base += 2
        for _ in range(max(base + rng.randint(-2, 2), 3)):
            item = rng.choices(CATALOG, weights=weights)[0]
            sku, _title, _default_channel, price, unit_cost, _oh, _rp, _w = item
            channel = rng.choices(list(CHANNEL_MIX), weights=list(CHANNEL_MIX.values()))[0]
            qty = rng.choices([1, 1, 1, 2, 3], weights=[55, 20, 10, 10, 5])[0]
            gross = round(price * qty, 2)
            defaults = CHANNEL_DEFAULTS.get(_normalize_channel(channel), CHANNEL_DEFAULTS["shopify"])
            fees = round(gross * defaults["fee_pct"] + defaults["fee_fixed"], 2)
            shipping = round(defaults["shipping"] * qty, 2)
            cogs = round(unit_cost * qty, 2)
            state = rng.choices(
                ["shipped", "shipped", "shipped", "delivered", "packed", "delayed"],
                weights=[30, 20, 15, 20, 8, 5],
            )[0]
            # Partial refunds keep the refund line honest without tripping the
            # per-order refund alerts that would bury the curated demo alerts.
            refund = round(gross * rng.uniform(0.2, 0.5), 2) if rng.random() < 0.04 else 0.0
            net = round(gross - fees - shipping - cogs - refund, 2)
            order_no += 1
            recorded = day.replace(
                hour=rng.randint(7, 22), minute=rng.randint(0, 59), second=rng.randint(0, 59)
            )
            db.session.add(ProfitFeedOrder(
                merchant_id=merchant_id,
                order_id=f"#{order_no}",
                channel=_normalize_channel(channel),
                items=qty,
                gross_revenue=gross,
                marketplace_fees=fees,
                cost_of_goods_sold=cogs,
                shipping_costs=shipping,
                ad_spend_attributed=0.0,
                refund_amount=refund,
                net_profit=net,
                state=state,
                recorded_at=recorded,
            ))
            db.session.add(UnifiedOrder(
                id=f"ord_{merchant_id}_{order_no}",
                merchant_id=merchant_id,
                channel=_normalize_channel(channel),
                revenue=gross,
                shipping_charged=shipping,
                tax=round(gross * 0.07, 2),
                status=state,
                fraud_score=rng.randint(1, 18),
                customer_id=f"cust_{rng.randint(1000, 9999)}",
                ship_to=rng.choice(["Miami, FL", "Austin, TX", "Brooklyn, NY", "Portland, OR", "Chicago, IL"]),
                created_at=recorded,
            ))
    db.session.commit()


def _seed_ad_spend(merchant_id, days=30):
    rng = random.Random(7)
    now = datetime.now(timezone.utc)
    for day_offset in range(days, -1, -1):
        day = now - timedelta(days=day_offset)
        for platform, base in DAILY_AD_SPEND.items():
            amount = round(base * rng.uniform(0.75, 1.25), 2)
            # Deliberate TikTok spike in the last two days so the alert has evidence.
            if platform == "tiktok" and day_offset <= 1:
                amount = round(base * 1.8, 2)
            db.session.add(AdSpendFeed(
                merchant_id=merchant_id,
                platform_source=platform,
                amount=amount,
                conversion_count=max(int(amount / rng.uniform(14, 30)), 1),
                recorded_at=day,
            ))
    db.session.commit()


ALERTS = [
    ("ad_spend_spike", "critical", "TikTok ad spend is outrunning its margin",
     "TikTok orders under $70 are now losing $11-$18 each after fees, shipping and attributed ad spend. Spend is up 80% over the last 48 hours.",
     "showcase:tiktok_spend"),
    ("inventory_runout", "high", "Satin Sleep Set runs out in 5 days",
     "68 units on hand, selling 13/day. Supplier lead time is 6 days — reorder today to avoid a stockout.",
     "showcase:AUR-SLK-001"),
    ("margin_drop", "high", "Amazon orders are breaking even at best",
     "Waffle Lounge Robe sells for $96 on Amazon and nets -$1.80 after fees, shipping and product cost. 7 of the last 11 Amazon orders lost money.",
     "showcase:amazon_margin"),
    ("refund_spike", "medium", "Refund rate on Shopify doubled",
     "4 of the last 40 Shopify orders were refunded (10%), against a baseline of 4.5%. Two cite sizing on the Satin Sleep Set.",
     "showcase:shopify_refunds"),
    ("shipping_delay", "medium", "6 orders past their promised delivery date",
     "Six orders shipped via USPS are past the promised date. Proactive notification reduces refund risk.",
     "showcase:shipping"),
]

# action_type, title, detail, payload, (weekly impact min, max)
ACTIONS = [
    ("adjust_ad_spend", "Pause the TikTok 'Sleep Set — Broad' ad set",
     "This ad set drove 14 low-value orders in 48 hours at a blended loss of $14.20 each. Pausing it protects roughly $198/week in margin.",
     {"platform": "tiktok", "adjustment": -100, "campaign": "Sleep Set — Broad"}, (164.0, 231.0)),
    ("reorder", "Reorder 240 units of Satin Sleep Set",
     "At 13 units/day and a 6-day lead time, stock runs out in 5 days. A 240-unit purchase order covers 18 days of demand and protects $2,120 in sales.",
     {"sku": "AUR-SLK-001", "quantity": 240, "supplier": "Lumen Textiles"}, (410.0, 530.0)),
    ("price_change", "Raise Waffle Lounge Robe to $104 on Amazon",
     "Amazon fees and shipping leave $1.80 of loss per unit at $96. An $8 increase restores a 6% net margin; competitor median is $109.",
     {"sku": "AUR-ROB-004", "new_price": 104.00, "old_price": 96.00, "channel": "amazon"}, (120.0, 186.0)),
]


def _seed_alerts_and_actions(merchant_id):
    now = datetime.now(timezone.utc)
    alert_ids = {}
    for idx, (atype, severity, title, detail, source_id) in enumerate(ALERTS):
        alert = Alert(
            merchant_id=merchant_id,
            alert_type=atype,
            severity=severity,
            title=title,
            detail=detail,
            source_id=source_id,
            status="open",
            created_at=now - timedelta(hours=idx * 3 + 1),
        )
        db.session.add(alert)
        db.session.flush()
        alert_ids[atype] = alert.id
    db.session.commit()

    alert_for_action = ["ad_spend_spike", "inventory_runout", "margin_drop"]
    for (action_type, title, detail, payload, impact), alert_type in zip(ACTIONS, alert_for_action):
        action = action_gate.create_action(
            merchant_id=merchant_id,
            action_type=action_type,
            title=title,
            detail=detail,
            payload=payload,
            alert_id=alert_ids.get(alert_type),
        )
        action.status = "pending"
        action.created_at = now - timedelta(minutes=25)
        evidence = ActionEvidence.query.filter_by(action_id=action.id).first()
        if evidence:
            evidence.expected_weekly_impact_min, evidence.expected_weekly_impact_max = impact
    db.session.commit()


def main():
    with app.app_context():
        db.create_all()

        profile = db.session.get(MerchantProfile, MERCHANT_ID) or MerchantProfile(merchant_id=MERCHANT_ID)
        profile.business_name = BUSINESS_NAME
        profile.admin_email = EMAIL
        profile.password_hash = generate_password_hash(PASSWORD, method="pbkdf2:sha256")
        profile.account_tier = "Enterprise AI Tier"
        profile.sandbox_status = "approved"
        profile.live_access_enabled = 1
        db.session.add(profile)
        db.session.commit()

        _clear(MERCHANT_ID)
        _seed_products(MERCHANT_ID)
        _seed_orders(MERCHANT_ID)
        _seed_ad_spend(MERCHANT_ID)
        _seed_alerts_and_actions(MERCHANT_ID)
        sandbox_demo._seed_demo_channels(MERCHANT_ID)

        bd = get_profit_breakdown(MERCHANT_ID)
        print(f"Seeded {MERCHANT_ID} ({EMAIL}): gross={bd['gross_revenue']}, net={bd['net_profit']}")
        if not os.environ.get("SHOWCASE_PASSWORD"):
            print(f"Generated login password: {PASSWORD}")
        print(f"orders={ProfitFeedOrder.query.filter_by(merchant_id=MERCHANT_ID).count()} "
              f"alerts={Alert.query.filter_by(merchant_id=MERCHANT_ID).count()} "
              f"actions={PendingAction.query.filter_by(merchant_id=MERCHANT_ID).count()}")


if __name__ == "__main__":
    main()
