"""Historical 14-day onboarding ingestion pipeline for Shopify.

Pulls the last 14 days of Shopify orders via the authenticated Admin REST API,
normalizes the payload into Vantav's unified order/product schema, and writes the
rows into the relational database. After ingestion it triggers the AI diagnostic
loops so the merchant's dashboard is populated with draft actions immediately.
"""
import datetime
import json
import logging
import os
import threading
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask

from models import db, Product, UnifiedOrder, OrderItem, BusinessMetric
import channels as channels_module
import coo_agent_mesh
import profit_regression

logger = logging.getLogger(__name__)

SHOPIFY_API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2024-01")


def _ensure_product(merchant_id: str, sku: str, title: str = "", unit_cost: float = 0.0) -> Product:
    """Get or create a Product row so OrderItem foreign-key constraints are satisfied."""
    if not sku:
        sku = f"UNKNOWN-{merchant_id[:8]}"
    product = Product.query.filter_by(sku=sku).first()
    if product:
        return product
    product = Product(
        sku=sku,
        merchant_id=merchant_id,
        title=title or sku,
        on_hand=0,
        inbound=0,
        reorder_point=10,
        unit_cost=Decimal(str(unit_cost)) if unit_cost else Decimal("0.0000"),
    )
    db.session.add(product)
    try:
        db.session.flush()
    except Exception:
        db.session.rollback()
        product = Product.query.filter_by(sku=sku).first() or Product(
            sku=sku,
            merchant_id=merchant_id,
            title=title or sku,
        )
    return product


def _parse_shopify_order(order: Dict[str, Any], merchant_id: str) -> Tuple[UnifiedOrder, List[OrderItem]]:
    """Normalize a Shopify order payload into a UnifiedOrder + OrderItem rows."""
    shopify_id = order.get("id")
    order_id = f"SHPFY_{shopify_id}"

    created_at = order.get("created_at") or datetime.datetime.utcnow().isoformat()
    if isinstance(created_at, str):
        try:
            created_dt = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            created_dt = datetime.datetime.utcnow()
    else:
        created_dt = datetime.datetime.utcnow()

    # Use subtotal_price as product revenue, shipping and tax separately.
    revenue = Decimal(str(order.get("subtotal_price", "0") or "0"))
    shipping_charged = Decimal(str(order.get("total_shipping_price_set", {}).get("shop_money", {}).get("amount", "0") or "0"))
    if shipping_charged == 0:
        try:
            shipping_charged = Decimal(str(order.get("shipping_lines", [{}])[0].get("price", "0") or "0"))
        except Exception:
            shipping_charged = Decimal("0.0000")
    tax = Decimal(str(order.get("total_tax", "0") or "0"))

    # Map Shopify order status to Vantav order status.
    cancelled_at = order.get("cancelled_at")
    financial_status = (order.get("financial_status") or "").lower()
    fulfillment_status = (order.get("fulfillment_status") or "").lower()
    if cancelled_at:
        status = "cancelled"
    elif financial_status == "refunded":
        status = "refunded"
    elif fulfillment_status == "fulfilled":
        status = "fulfilled"
    else:
        status = "pending"

    customer = order.get("customer") or {}
    customer_id = str(customer.get("id") or "") or None
    shipping_address = order.get("shipping_address") or {}
    ship_to = {
        "name": " ".join(filter(None, [shipping_address.get("first_name"), shipping_address.get("last_name")])),
        "address1": shipping_address.get("address1", ""),
        "city": shipping_address.get("city", ""),
        "province": shipping_address.get("province", ""),
        "zip": shipping_address.get("zip", ""),
        "country": shipping_address.get("country", ""),
    }

    unified = UnifiedOrder(
        id=order_id,
        merchant_id=merchant_id,
        channel="shopify",
        revenue=revenue,
        shipping_charged=shipping_charged,
        tax=tax,
        status=status,
        fraud_score=0,
        customer_id=customer_id,
        ship_to=ship_to,
        created_at=created_dt,
    )

    items: List[OrderItem] = []
    for line in order.get("line_items", []):
        variant_id = line.get("variant_id")
        sku = (line.get("sku") or f"SHPFY-{variant_id}" or f"SHPFY-{shopify_id}-line")
        title = line.get("title", "")
        qty = int(line.get("quantity", 1) or 1)
        unit_price = Decimal(str(line.get("price", "0") or "0"))

        product = _ensure_product(merchant_id, sku, title=title)
        unit_cost = Decimal(product.unit_cost or 0)

        items.append(OrderItem(
            order_id=order_id,
            sku=sku,
            qty=qty,
            unit_price=unit_price,
            unit_cost=unit_cost,
        ))

    return unified, items


def _write_orders(merchant_id: str, orders: List[Dict[str, Any]]) -> Tuple[int, int]:
    """Persist normalized Shopify orders and line items, skipping duplicates."""
    order_count = 0
    item_count = 0
    for raw in orders:
        try:
            order_id = f"SHPFY_{raw.get('id')}"
            if UnifiedOrder.query.get(order_id):
                continue

            unified, items = _parse_shopify_order(raw, merchant_id)
            db.session.add(unified)
            for item in items:
                db.session.add(item)
                item_count += 1
            order_count += 1

            # Commit in small batches to keep memory low.
            if (order_count + item_count) % 50 == 0:
                db.session.commit()
        except Exception as e:
            logger.warning(f"[Historical Ingestion] Skipping Shopify order {raw.get('id')}: {e}")
            db.session.rollback()
            continue

    try:
        db.session.commit()
    except Exception as e:
        logger.error(f"[Historical Ingestion] Final commit failed: {e}")
        db.session.rollback()

    return order_count, item_count


def _run_diagnostics(merchant_id: str) -> Dict[str, Any]:
    """Run profit regression and COO diagnostics after historical data is loaded."""
    diagnostics: Dict[str, Any] = {"regression_actions": [], "coo_actions": [], "errors": []}
    try:
        diagnostics["regression_actions"] = profit_regression.run_regression_for_merchant(
            merchant_id, lookback_days=14, create_actions=True
        )
    except Exception as e:
        logger.error(f"[Historical Ingestion] Regression failed for {merchant_id}: {e}")
        diagnostics["errors"].append(f"regression: {e}")

    try:
        diagnostics["coo_actions"] = coo_agent_mesh.run_diagnostic(
            merchant_id, days=14, create_actions=True
        )
    except Exception as e:
        logger.error(f"[Historical Ingestion] COO diagnostic failed for {merchant_id}: {e}")
        diagnostics["errors"].append(f"coo: {e}")
    return diagnostics


def _summarize(merchant_id: str, orders: int, items: int, diagnostics: Dict[str, Any]) -> str:
    total_actions = len(diagnostics.get("regression_actions", [])) + len(diagnostics.get("coo_actions", []))
    sku_count = Product.query.filter_by(merchant_id=merchant_id).count()
    net_profit = 0.0
    try:
        from profit_feed import get_kpis
        kpis = get_kpis(merchant_id, window_days=14)
        net_profit = float((kpis or {}).get("net_profit", 0.0) or 0.0)
    except Exception:
        pass
    return (
        f"Shopify 14-day historical sync complete. Imported {orders} orders and {items} line items. "
        f"{sku_count} SKUs tracked, 14-day net profit ${net_profit:,.2f}. {total_actions} draft action(s) staged."
    )


def execute_shopify_14d_history_harvest(merchant_id: str, shop_domain: str, token: str, app: Flask):
    """Background worker: pull 14 days of Shopify orders and populate diagnostics."""
    with app.app_context():
        logger.info(f"[Historical Ingestion] Starting 14-day harvest for {merchant_id} on {shop_domain}")

        time_window = datetime.datetime.utcnow() - datetime.timedelta(days=14)
        created_at_min = time_window.isoformat() + "Z"

        endpoint = f"https://{shop_domain}/admin/api/{SHOPIFY_API_VERSION}/orders.json"
        headers = {
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
        }
        params = {
            "status": "any",
            "created_at_min": created_at_min,
            "limit": 250,
        }

        orders_list: List[Dict[str, Any]] = []
        try:
            response = requests.get(endpoint, headers=headers, params=params, timeout=30)
            if response.status_code != 200:
                logger.error(
                    f"[Historical Ingestion] Shopify REST endpoint rejected token for {merchant_id}: {response.status_code}"
                )
                return {"status": "error", "detail": f"Shopify API returned {response.status_code}"}
            orders_list = response.json().get("orders", [])
        except Exception as e:
            logger.error(f"[Historical Ingestion] Shopify API request failed: {e}")
            return {"status": "error", "detail": str(e)}

        logger.info(f"[Historical Ingestion] Retrieved {len(orders_list)} historical orders for {merchant_id}")

        order_count, item_count = _write_orders(merchant_id, orders_list)
        diagnostics = _run_diagnostics(merchant_id)
        summary = _summarize(merchant_id, order_count, item_count, diagnostics)

        try:
            db.session.add(BusinessMetric(
                merchant_id=merchant_id,
                total_unified_balance=0.0,
                true_net_profit=0.0,
                gross_revenue=0.0,
                ai_briefing=summary,
            ))
            db.session.commit()
        except Exception as e:
            logger.error(f"[Historical Ingestion] Failed to write summary for {merchant_id}: {e}")
            db.session.rollback()

        logger.info(f"[Historical Ingestion] Completed for {merchant_id}: {order_count} orders, {len(diagnostics.get('coo_actions', []))} COO actions")


def _get_shopify_credentials(merchant_id: str) -> Optional[Tuple[str, str]]:
    """Look up the stored Shopify shop domain and decoded access token for a merchant."""
    from models import TenantOAuthToken
    token_row = TenantOAuthToken.query.filter_by(merchant_id=merchant_id, platform_id="shopify").first()
    if not token_row:
        return None
    try:
        access_token = channels_module.get_token(merchant_id, "shopify")
    except Exception:
        access_token = channels_module._decode_token(token_row.access_token_encrypted)
    if not access_token:
        return None
    return token_row.shop_domain, access_token


def trigger_onboarding_harvest(merchant_id: str, shop_domain: Optional[str] = None, token: Optional[str] = None, app: Optional[Flask] = None) -> Dict[str, Any]:
    """Start the 14-day Shopify historical harvest in a background thread."""
    if not shop_domain or not token:
        creds = _get_shopify_credentials(merchant_id)
        if not creds:
            return {"status": "error", "detail": "No Shopify credentials found"}
        shop_domain, token = creds

    if not app:
        from flask import current_app
        app = current_app._get_current_object()

    t = threading.Thread(
        target=execute_shopify_14d_history_harvest,
        args=(merchant_id, shop_domain, token, app),
        daemon=True,
        name=f"hist-ingest-{merchant_id[:8]}",
    )
    t.start()
    return {"status": "processing", "message": "Historical data ingestion worker dispatched."}
