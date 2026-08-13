"""Shopify Admin API sync for orders and product catalog."""
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

import requests

from models import db, MerchantChannel, TenantOAuthToken, MerchantSetting
import profit_feed
import channels as channels_module

logger = logging.getLogger(__name__)

SHOPIFY_API_VERSION = "2024-01"


def _get_token(merchant_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (shop_domain, access_token) for a merchant's Shopify connection."""
    token = TenantOAuthToken.query.filter_by(merchant_id=merchant_id, platform_id="shopify").first()
    if not token:
        return None, None
    return token.shop_domain, channels_module.get_token(merchant_id, "shopify") or ""


def _shopify_get(shop_domain: str, access_token: str, path: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
    """Make an authenticated GET request to the Shopify Admin API."""
    url = f"https://{shop_domain}/admin/api/{SHOPIFY_API_VERSION}/{path}.json"
    headers = {"X-Shopify-Access-Token": access_token}
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code != 200:
        raise ValueError(f"Shopify API error ({resp.status_code}): {resp.text[:500]}")
    return resp.json()


def _order_state(order: Dict[str, Any]) -> str:
    """Map Shopify order status to a profit-feed state."""
    if order.get("cancelled_at"):
        return "cancelled"
    financial = (order.get("financial_status") or "").lower()
    fulfillment = (order.get("fulfillment_status") or "").lower()
    if financial in ("refunded", "partially_refunded"):
        return "refunded"
    if fulfillment in ("shipped", "fulfilled") or order.get("fulfillments"):
        return "shipped"
    if financial == "paid":
        return "packed"
    return "delayed"


def _parse_refund_amount(order: Dict[str, Any]) -> float:
    """Best-effort refund total from the order payload."""
    try:
        return float(order.get("total_refund_amount") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def sync_orders(merchant_id: str, limit: int = 250) -> Tuple[int, List[str]]:
    """Pull recent Shopify orders and feed the profit engine.

    Returns the number of orders synced and a list of any errors.
    """
    shop, token = _get_token(merchant_id)
    if not shop or not token:
        raise ValueError("Shopify is not connected for this merchant")

    params = {
        "status": "any",
        "limit": min(limit, 250),
        "fields": "id,name,total_price,line_items,financial_status,fulfillment_status,fulfillments,total_refund_amount,cancelled_at",
    }
    data = _shopify_get(shop, token, "orders", params)
    orders = data.get("orders", [])

    count = 0
    for order in orders:
        try:
            order_id = f"{merchant_id}:shopify:{order.get('name', order['id'])}"
            gross = float(order.get("total_price") or 0.0)
            items = order.get("line_items") or []
            state = _order_state(order)
            refund = _parse_refund_amount(order)
            order_items = []
            for li in items:
                if not isinstance(li, dict):
                    continue
                sku = li.get("sku") or li.get("product_id") or li.get("variant_id") or ""
                qty = li.get("quantity") or 1
                price = li.get("price") or 0.0
                title = li.get("title") or li.get("name") or sku
                if sku:
                    order_items.append({
                        "sku": str(sku).strip(),
                        "qty": int(qty or 1),
                        "price": float(price or 0.0),
                        "title": title,
                    })
            profit_feed.record_order(
                merchant_id=merchant_id,
                channel="shopify",
                order_id=order_id,
                gross_revenue=gross,
                items=len(items),
                state=state,
                refund_amount=refund,
                order_items=order_items,
            )
            count += 1
        except Exception as e:
            logger.error(f"[Shopify Sync] Failed to import order {order.get('id')}: {e}")

    # Update channel sync timestamp and pending order count.
    mch = MerchantChannel.query.filter_by(merchant_id=merchant_id, channel_id="shopify").first()
    if mch:
        mch.pending_orders = max(0, len(orders) - count)  # rough pending count
    tk = TenantOAuthToken.query.filter_by(merchant_id=merchant_id, platform_id="shopify").first()
    if tk:
        tk.updated_at = datetime.utcnow()
    db.session.commit()
    return count, []


def sync_products(merchant_id: str, limit: int = 250) -> int:
    """Pull the Shopify product catalog and store it as merchant settings JSON.

    Returns the number of variants synced.
    """
    shop, token = _get_token(merchant_id)
    if not shop or not token:
        raise ValueError("Shopify is not connected for this merchant")

    params = {
        "limit": min(limit, 250),
        "fields": "id,title,status,product_type,variants,images",
    }
    data = _shopify_get(shop, token, "products", params)
    products = data.get("products", [])

    catalog = []
    for product in products:
        base_image = ""
        images = product.get("images") or []
        if images:
            base_image = images[0].get("src", "")
        for variant in product.get("variants") or []:
            try:
                catalog.append({
                    "product_id": str(product.get("id", "")),
                    "variant_id": str(variant.get("id", "")),
                    "title": product.get("title", ""),
                    "sku": variant.get("sku", ""),
                    "price": float(variant.get("price") or 0.0),
                    "inventory_quantity": int(variant.get("inventory_quantity") or 0),
                    "image_url": base_image,
                    "status": product.get("status", ""),
                    "product_type": product.get("product_type", ""),
                })
            except (TypeError, ValueError) as e:
                logger.warning(f"[Shopify Sync] Skipping malformed variant {variant.get('id')}: {e}")

    # Persist as merchant setting keyed by platform.
    setting = MerchantSetting.query.filter_by(merchant_id=merchant_id, setting_key="shopify_products").first()
    if not setting:
        setting = MerchantSetting(merchant_id=merchant_id, setting_key="shopify_products")
        db.session.add(setting)
    setting.setting_value = json.dumps(catalog)
    db.session.commit()
    return len(catalog)


def sync_shopify(merchant_id: str, orders_limit: int = 250, products_limit: int = 250) -> Dict[str, Any]:
    """One-call sync of Shopify orders and product catalog."""
    order_count, _ = sync_orders(merchant_id, limit=orders_limit)
    product_count = sync_products(merchant_id, limit=products_limit)
    return {"orders_synced": order_count, "products_synced": product_count}


def get_products(merchant_id: str) -> List[Dict[str, Any]]:
    """Return the last-synced Shopify product catalog for a merchant."""
    setting = MerchantSetting.query.filter_by(merchant_id=merchant_id, setting_key="shopify_products").first()
    if not setting or not setting.setting_value:
        return []
    try:
        return json.loads(setting.setting_value)
    except json.JSONDecodeError:
        return []
