"""TikTok Shop Open Platform sync for orders and products.

Implements the HMAC-SHA256 signature algorithm used by TikTok Shop's
open-api.tiktokglobalshop.com endpoints, based on the documented
"sign your API request" flow.
"""
import hashlib
import hmac
import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

import requests

from models import db, MerchantChannel, TenantOAuthToken, MerchantSetting
import channels as channels_module
import profit_feed

logger = logging.getLogger(__name__)

BASE_URL = "https://open-api.tiktokglobalshop.com"
VERSION = "202309"


def _get_credentials(merchant_id: str) -> Dict[str, str]:
    """Return TikTok app credentials for a merchant."""
    token = TenantOAuthToken.query.filter_by(merchant_id=merchant_id, platform_id="tiktok").first()
    if not token:
        return {}
    try:
        creds = json.loads(channels_module.get_token(merchant_id, "tiktok") or "{}")
    except json.JSONDecodeError:
        return {}
    return {
        "app_key": creds.get("app_key", ""),
        "app_secret": creds.get("app_secret", ""),
        "access_token": creds.get("access_token", ""),
        "shop_id": creds.get("shop_id", ""),
        "shop_cipher": creds.get("shop_cipher", ""),
    }


def _sign(url: str, app_secret: str, body: Any = None) -> Tuple[str, int]:
    """Generate a TikTok Shop request signature and timestamp."""
    timestamp = int(time.time())
    decoded_url = unquote(url)
    parts = decoded_url.split("?", 1)
    if len(parts) != 2:
        raise ValueError("Invalid URL for signing: missing query string")
    match = re.search(r"\.com(.*?)(?:\?|$)", decoded_url)
    if not match:
        raise ValueError("Invalid URL for signing: cannot extract path")
    path = match.group(1)
    query = parts[1]

    params: Dict[str, str] = {}
    for pair in query.split("&"):
        if "=" in pair:
            key, value = pair.split("=", 1)
            params[key] = value

    excluded = {"app_secret", "token", "access_token", "sign"}
    keys = sorted(k for k in params if k not in excluded)
    input_str = "".join(k + params[k] for k in keys)

    body_text = json.dumps(body) if body is not None else ""
    plain_text = f"{app_secret}{path}{input_str}{body_text}{app_secret}"
    signature = hmac.new(app_secret.encode("utf-8"), plain_text.encode("utf-8"), hashlib.sha256).hexdigest()
    return signature, timestamp


def _request(
    method: str,
    endpoint: str,
    credentials: Dict[str, str],
    query: str = "",
    body: Any = None,
) -> Dict[str, Any]:
    """Make a signed request to the TikTok Shop Open Platform."""
    app_key = credentials.get("app_key", "")
    app_secret = credentials.get("app_secret", "")
    access_token = credentials.get("access_token", "")
    shop_id = credentials.get("shop_id", "")
    shop_cipher = credentials.get("shop_cipher", "")
    if not all([app_key, app_secret, access_token]):
        raise ValueError("TikTok credentials incomplete: app_key, app_secret, and access_token are required")

    url = f"{BASE_URL}{endpoint}?access_token={access_token}&app_key={app_key}"
    if shop_cipher:
        url += f"&shop_cipher={shop_cipher}"
    if shop_id:
        url += f"&shop_id={shop_id}"
    url += f"&version={VERSION}"
    if query:
        if not query.startswith("&"):
            query = "&" + query
        url += query

    signature, timestamp = _sign(url, app_secret, body)
    url += f"&timestamp={timestamp}&sign={signature}"

    headers = {
        "x-tts-access-token": access_token,
        "Content-Type": "application/json",
    }
    if method.upper() == "GET":
        resp = requests.get(url, headers=headers, data=json.dumps(body) if body is not None else None, timeout=30)
    else:
        resp = requests.post(url, headers=headers, data=json.dumps(body) if body is not None else "{}", timeout=30)

    if resp.status_code != 200:
        raise ValueError(f"TikTok API error ({resp.status_code}): {resp.text[:500]}")
    data = resp.json()
    if data.get("code") != 0:
        raise ValueError(f"TikTok API returned error {data.get('code')}: {data.get('message')}")
    return data.get("data", {})


def _map_order_status(status: str) -> str:
    s = (status or "").upper()
    if s == "CANCELLED":
        return "cancelled"
    if s in ("DELIVERED", "COMPLETED", "IN_TRANSIT"):
        return "shipped"
    if s in ("AWAITING_SHIPMENT", "AWAITING_COLLECTION", "ON_HOLD"):
        return "packed"
    return "delayed"


def sync_orders(merchant_id: str) -> int:
    """Pull TikTok Shop orders and feed the profit engine.

    Searches across common order statuses and accumulates all returned orders.
    Returns the number of orders synced.
    """
    creds = _get_credentials(merchant_id)
    if not creds:
        raise ValueError("TikTok is not connected for this merchant")

    orders_seen: Dict[str, Dict[str, Any]] = {}
    statuses = ["UNPAID", "ON_HOLD", "AWAITING_SHIPMENT", "AWAITING_COLLECTION", "IN_TRANSIT", "DELIVERED", "COMPLETED", "CANCELLED"]

    for status in statuses:
        try:
            data = _request(
                "POST",
                f"/order/{VERSION}/orders/search",
                creds,
                query="page_size=100",
                body={"order_status": status},
            )
            for order in data.get("orders", []):
                orders_seen[order.get("order_id")] = order
        except Exception as e:
            logger.error(f"[TikTok Sync] Failed status {status} for {merchant_id}: {e}")

    count = 0
    for order in orders_seen.values():
        try:
            order_id = f"{merchant_id}:tiktok:{order.get('order_id')}"
            line_items = order.get("line_items", [])
            items = sum(int(li.get("quantity", 1) or 1) for li in line_items)
            gross = 0.0
            for li in line_items:
                price = li.get("sale_price") or {}
                amount = float(price.get("amount", 0.0) or 0.0)
                gross += amount * int(li.get("quantity", 1) or 1)

            state = _map_order_status(order.get("order_status"))
            profit_feed.record_order(
                merchant_id=merchant_id,
                channel="tiktok",
                order_id=order_id,
                gross_revenue=gross,
                items=items,
                state=state,
            )
            count += 1
        except Exception as e:
            logger.error(f"[TikTok Sync] Failed to import order {order.get('order_id')}: {e}")

    mch = MerchantChannel.query.filter_by(merchant_id=merchant_id, channel_id="tiktok").first()
    if mch:
        mch.pending_orders = max(0, len(orders_seen) - count)
    tk = TenantOAuthToken.query.filter_by(merchant_id=merchant_id, platform_id="tiktok").first()
    if tk:
        tk.updated_at = datetime.utcnow()
    db.session.commit()
    return count


def sync_products(merchant_id: str) -> int:
    """Pull the TikTok Shop product catalog and store it as merchant settings JSON."""
    creds = _get_credentials(merchant_id)
    if not creds:
        raise ValueError("TikTok is not connected for this merchant")

    data = _request(
        "GET",
        f"/product/{VERSION}/products",
        creds,
        query="page_size=100&status=ALL",
    )
    products = data.get("products", [])

    catalog = []
    for product in products:
        product_id = str(product.get("product_id", ""))
        title = product.get("product_name", "")
        status = product.get("status", "")
        base_price = float((product.get("price") or {}).get("amount", 0.0) or 0.0)
        image_url = ""

        for sku in product.get("skus", []):
            try:
                price = sku.get("price") or {}
                amount = float(price.get("amount", base_price) or base_price)
                inventory = sum(int(s.get("available_stock", 0) or 0) for s in sku.get("stock_infos", []))
                catalog.append({
                    "product_id": product_id,
                    "variant_id": str(sku.get("id", "")),
                    "title": title,
                    "sku": sku.get("seller_sku", ""),
                    "price": amount,
                    "inventory_quantity": inventory,
                    "image_url": image_url,
                    "status": status,
                    "product_type": "",
                })
            except (TypeError, ValueError) as e:
                logger.warning(f"[TikTok Sync] Skipping malformed SKU {sku.get('id')}: {e}")

    setting = MerchantSetting.query.filter_by(merchant_id=merchant_id, setting_key="tiktok_products").first()
    if not setting:
        setting = MerchantSetting(merchant_id=merchant_id, setting_key="tiktok_products")
        db.session.add(setting)
    setting.setting_value = json.dumps(catalog)
    db.session.commit()
    return len(catalog)


def sync_tiktok(merchant_id: str) -> Dict[str, Any]:
    """One-call sync of TikTok Shop orders and product catalog."""
    order_count = sync_orders(merchant_id)
    product_count = sync_products(merchant_id)
    return {"orders_synced": order_count, "products_synced": product_count}


def get_products(merchant_id: str) -> List[Dict[str, Any]]:
    """Return the last-synced TikTok Shop product catalog for a merchant."""
    setting = MerchantSetting.query.filter_by(merchant_id=merchant_id, setting_key="tiktok_products").first()
    if not setting or not setting.setting_value:
        return []
    try:
        return json.loads(setting.setting_value)
    except json.JSONDecodeError:
        return []
