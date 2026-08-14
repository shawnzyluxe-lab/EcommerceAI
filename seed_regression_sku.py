"""Seed historical orders and daily costs for the regression chart demo SKU.

This is an admin-only utility that populates a merchant with enough per-day
revenue, COGS, and ad-spend rows for `profit_regression.build_waterfall_dataset`
to produce a meaningful OLS trend (declining net profit as ad spend rises).
"""

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from models import db, UnifiedOrder
from unified_ingest import record_daily_cost, record_unified_order, upsert_product, upsert_supplier


DEFAULT_SKU = "SKU-404-PODS"


def seed_sku_for_regression(
    merchant_id: str,
    sku: str = DEFAULT_SKU,
    days: int = 14,
) -> dict:
    """Create or extend per-day order + cost data for a demo SKU.

    Idempotent by order_id: running twice will skip already seeded orders.
    """
    supplier = upsert_supplier(merchant_id, name="Demo Supplier", lead_days=10)
    product = upsert_product(
        merchant_id=merchant_id,
        sku=sku,
        title="Viral Jacket",
        unit_cost=20.0,
        on_hand=500,
        supplier_id=supplier.id,
    )
    db.session.commit()

    base_revenue = 2400.0
    base_ad_spend = 400.0
    created = 0

    for i in range(days, -1, -1):
        day = date.today() - timedelta(days=i)
        order_id = f"seed-{sku}-{day.isoformat()}"
        if UnifiedOrder.query.filter_by(id=order_id).first():
            continue

        # Slightly rising revenue, but ad spend climbs faster -> degrading profit.
        revenue = base_revenue + (days - i) * 50.0
        ad_spend = base_ad_spend + (days - i) * 100.0
        unit_price = 120.0
        qty = max(1, round(revenue / unit_price))

        order_items = [
            {
                "sku": sku,
                "qty": qty,
                "unit_price": unit_price,
                "unit_cost": float(product.unit_cost or 20.0),
                "title": product.title or "Viral Jacket",
            }
        ]

        timestamp = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        record_unified_order(
            merchant_id=merchant_id,
            channel="shopify",
            order_id=order_id,
            order_items=order_items,
            revenue=revenue,
            status="shipped",
            created_at=timestamp,
            default_supplier_id=supplier.id,
            shipping_cost=5.0 * qty,
            fee=revenue * 0.029 + 0.30,
            refund=0.0,
            tax=0.0,
        )

        # Add the day's ad spend on top of the order-level cost allocation.
        record_daily_cost(
            merchant_id=merchant_id,
            sku=sku,
            log_date=day,
            ad_spend=ad_spend,
        )
        created += 1

    db.session.commit()
    return {"merchant_id": merchant_id, "sku": sku, "days": days, "orders_created": created}


if __name__ == "__main__":
    import os
    from app import app

    merchant_id = os.environ.get("MERCHANT_ID", "merchant_shawn_01")
    with app.app_context():
        result = seed_sku_for_regression(merchant_id)
        print(result)
