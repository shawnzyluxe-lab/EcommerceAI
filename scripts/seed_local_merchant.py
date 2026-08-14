#!/usr/bin/env python3
"""Seed a local test merchant with a product for webhook simulations."""

import os
import sys

from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from models import db, MerchantProfile, SaaSBilling, BusinessMemory, WorkspaceSeat, Product

MERCHANT_ID = os.environ.get("MERCHANT_ID", "merchant_shawn_01")
EMAIL = os.environ.get("MERCHANT_EMAIL", "local@vantavcommerce.com")
TIER = os.environ.get("SELECTED_TIER", "Vantav Growth")


def seed():
    with app.app_context():
        profile = MerchantProfile.query.get(MERCHANT_ID)
        if not profile:
            profile = MerchantProfile(
                merchant_id=MERCHANT_ID,
                business_name="Local Test Store",
                admin_email=EMAIL,
                account_tier=TIER,
                password_hash=generate_password_hash("LocalPass123!", method="pbkdf2:sha256"),
                sandbox_status="approved",
                live_access_enabled=1,
            )
            db.session.add(profile)
            db.session.flush()

        billing = SaaSBilling.query.get(MERCHANT_ID)
        if not billing:
            billing = SaaSBilling(
                merchant_id=MERCHANT_ID,
                current_plan=TIER,
                metered_usage_units=0,
                accrued_invoice_value=0.0,
            )
            db.session.add(billing)

        memory = BusinessMemory.query.filter_by(merchant_id=MERCHANT_ID).first()
        if not memory:
            memory = BusinessMemory(merchant_id=MERCHANT_ID)
            from tier_manager import TierManager
            meta = TierManager.get_tier_meta(TIER)
            memory.max_authorized_seats = int(meta.get("max_users", 10))
            memory.current_active_seats = 1
            db.session.add(memory)

        seat = WorkspaceSeat.query.filter_by(merchant_id=MERCHANT_ID, user_email=EMAIL).first()
        if not seat:
            db.session.add(WorkspaceSeat(merchant_id=MERCHANT_ID, user_email=EMAIL, role="admin"))

        product = Product.query.filter_by(sku="SKU-TRACK-JACKET", merchant_id=MERCHANT_ID).first()
        if not product:
            db.session.add(Product(
                sku="SKU-TRACK-JACKET",
                merchant_id=MERCHANT_ID,
                title="Oversized Utility Cyber Jacket",
                unit_cost=35.0,
                on_hand=500,
                reorder_point=50,
            ))

        db.session.commit()
        print(f"[SEED] Merchant {MERCHANT_ID} and test product ready.")


if __name__ == "__main__":
    seed()
