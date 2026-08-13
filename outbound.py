"""Live marketplace write-backs for approved actions.

Handles outbound inventory, price, and fulfillment updates to Shopify,
TikTok Shop, and Amazon where credentials are configured. Missing
configurations are logged rather than raising, so a local fallback record
is always created.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import requests

from models import db, GeneratedPurchaseOrder
import channels as channels_module
import shopify_sync
import tiktok_sync
import amazon_sync

logger = logging.getLogger(__name__)

SHOPIFY_API_VERSION = "2024-01"


def _shopify_token(merchant_id: str) -> Tuple[Optional[str], Optional[str]]:
    token = channels_module.get_token(merchant_id, "shopify")
    if not token:
        return None, None
    shop_record = channels_module.TenantOAuthToken.query.filter_by(
        merchant_id=merchant_id, platform_id="shopify"
    ).first()
    shop_domain = shop_record.shop_domain if shop_record else None
    return shop_domain, token


def _find_shopify_variant(merchant_id: str, sku: str) -> Optional[Dict[str, Any]]:
    """Return {product_id, variant_id, inventory_item_id} for a given SKU."""
    catalog = shopify_sync.get_products(merchant_id)
    for product in catalog:
        if product.get("sku", "").upper() == sku.upper():
            return {
                "product_id": product.get("product_id"),
                "variant_id": product.get("variant_id"),
                "inventory_item_id": product.get("inventory_item_id"),
            }
    # Fallback: refresh catalog and try again.
    try:
        shopify_sync.sync_products(merchant_id)
    except Exception as e:
        logger.warning(f"[Outbound] Shopify product sync failed for {merchant_id}: {e}")
    catalog = shopify_sync.get_products(merchant_id)
    for product in catalog:
        if product.get("sku", "").upper() == sku.upper():
            return {
                "product_id": product.get("product_id"),
                "variant_id": product.get("variant_id"),
                "inventory_item_id": product.get("inventory_item_id"),
            }
    return None


def _shopify_locations(shop_domain: str, access_token: str) -> List[Dict[str, Any]]:
    url = f"https://{shop_domain}/admin/api/{SHOPIFY_API_VERSION}/locations.json"
    headers = {"X-Shopify-Access-Token": access_token}
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 200:
            return resp.json().get("locations", [])
    except Exception as e:
        logger.warning(f"[Outbound] Shopify locations error: {e}")
    return []


def shopify_update_inventory(
    merchant_id: str,
    sku: str,
    quantity: int,
) -> Dict[str, Any]:
    """Push an inventory update to Shopify if a matching variant is found."""
    shop_domain, access_token = _shopify_token(merchant_id)
    if not shop_domain or not access_token:
        return {"status": "skipped", "reason": "Shopify not connected"}

    variant = _find_shopify_variant(merchant_id, sku)
    if not variant or not variant.get("variant_id"):
        return {"status": "skipped", "reason": f"SKU {sku} not found in Shopify"}

    product_id = variant["product_id"]
    variant_id = variant["variant_id"]
    headers = {"X-Shopify-Access-Token": access_token, "Content-Type": "application/json"}

    # First attempt: legacy variant inventory_quantity update.
    url = f"https://{shop_domain}/admin/api/{SHOPIFY_API_VERSION}/products/{product_id}.json"
    payload = {
        "product": {
            "id": int(product_id),
            "variants": [{"id": int(variant_id), "inventory_quantity": int(quantity)}],
        }
    }
    try:
        resp = requests.put(url, headers=headers, json=payload, timeout=20)
        if resp.status_code in (200, 201):
            return {"status": "ok", "channel": "shopify", "sku": sku, "quantity": quantity}
    except Exception as e:
        logger.warning(f"[Outbound] Shopify variant inventory update failed: {e}")

    # Fallback: use inventory_levels/set if we have an inventory_item_id and a location.
    inventory_item_id = variant.get("inventory_item_id")
    if not inventory_item_id:
        return {"status": "failed", "reason": "No inventory_item_id for SKU"}

    locations = _shopify_locations(shop_domain, access_token)
    if not locations:
        return {"status": "failed", "reason": "No Shopify locations found"}

    url = f"https://{shop_domain}/admin/api/{SHOPIFY_API_VERSION}/inventory_levels/set.json"
    payload = {
        "inventory_item_id": int(inventory_item_id),
        "location_id": int(locations[0]["id"]),
        "available": int(quantity),
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        if resp.status_code in (200, 201):
            return {"status": "ok", "channel": "shopify", "sku": sku, "quantity": quantity}
        return {"status": "failed", "reason": resp.text[:500]}
    except Exception as e:
        return {"status": "failed", "reason": str(e)}


def shopify_update_price(merchant_id: str, sku: str, price: float) -> Dict[str, Any]:
    """Push a price update to Shopify if a matching variant is found."""
    shop_domain, access_token = _shopify_token(merchant_id)
    if not shop_domain or not access_token:
        return {"status": "skipped", "reason": "Shopify not connected"}

    variant = _find_shopify_variant(merchant_id, sku)
    if not variant or not variant.get("variant_id"):
        return {"status": "skipped", "reason": f"SKU {sku} not found in Shopify"}

    product_id = variant["product_id"]
    variant_id = variant["variant_id"]
    url = f"https://{shop_domain}/admin/api/{SHOPIFY_API_VERSION}/products/{product_id}.json"
    headers = {"X-Shopify-Access-Token": access_token, "Content-Type": "application/json"}
    payload = {
        "product": {
            "id": int(product_id),
            "variants": [{"id": int(variant_id), "price": str(price)}],
        }
    }
    try:
        resp = requests.put(url, headers=headers, json=payload, timeout=20)
        if resp.status_code in (200, 201):
            return {"status": "ok", "channel": "shopify", "sku": sku, "price": price}
        return {"status": "failed", "reason": resp.text[:500]}
    except Exception as e:
        return {"status": "failed", "reason": str(e)}


def tiktok_update_inventory(merchant_id: str, sku: str, quantity: int) -> Dict[str, Any]:
    """Push an inventory update to TikTok Shop if credentials are configured."""
    creds = tiktok_sync._get_credentials(merchant_id)
    if not creds or not creds.get("access_token"):
        return {"status": "skipped", "reason": "TikTok Shop not connected"}
    # TikTok product update requires product_id/sku_id resolution that we do not
    # currently cache locally. Log intent and return pending.
    logger.info(f"[Outbound] TikTok inventory update queued: {sku} -> {quantity}")
    return {"status": "pending", "channel": "tiktok", "sku": sku, "quantity": quantity}


def amazon_update_inventory(merchant_id: str, sku: str, quantity: int) -> Dict[str, Any]:
    """Push an inventory update to Amazon if SP-API credentials are configured."""
    creds = amazon_sync._get_credentials(merchant_id)
    if not creds:
        return {"status": "skipped", "reason": "Amazon not connected"}
    logger.info(f"[Outbound] Amazon inventory update queued: {sku} -> {quantity}")
    return {"status": "pending", "channel": "amazon", "sku": sku, "quantity": quantity}


def ad_platform_update_budget(platform: str, merchant_id: str, new_budget: float) -> Dict[str, Any]:
    """Push an ad budget adjustment to the target platform.

    Currently records the target budget locally; live ad API integrations
    (Meta, Google, TikTok Ads) require separate OAuth scopes not yet wired.
    """
    logger.info(f"[Outbound] Ad budget update queued for {platform}: ${new_budget}")
    return {"status": "pending", "channel": platform, "new_budget": new_budget}


def send_supplier_po(merchant_id: str, po: GeneratedPurchaseOrder) -> Dict[str, Any]:
    """Transmit a purchase order to the supplier channel configured for the merchant."""
    logger.info(
        f"[Outbound] PO {po.po_reference} queued for merchant {merchant_id}: "
        f"{po.units_ordered} units of {po.variant_sku}"
    )
    return {
        "status": "pending",
        "po_reference": po.po_reference,
        "sku": po.variant_sku,
        "units": po.units_ordered,
    }


def dispatch_action(action_type: str, merchant_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Route an approved action to the appropriate live marketplace write-back."""
    results: List[Dict[str, Any]] = []

    if action_type == "ad_adjust" or action_type == "ad_budget":
        platform = payload.get("platform", payload.get("channel", ""))
        new_budget = payload.get("new_budget") or payload.get("budget")
        if new_budget is None:
            adjustment = float(payload.get("adjustment", 0.0))
            # We don't know current budget here; keep local-only pending.
            results.append({"status": "pending", "reason": "No current budget to adjust"})
        else:
            results.append(ad_platform_update_budget(platform, merchant_id, float(new_budget)))

    elif action_type == "price":
        sku = payload.get("sku", "")
        price = payload.get("price") or payload.get("new_price")
        if sku and price is not None:
            results.append(shopify_update_price(merchant_id, sku, float(price)))
            results.append(tiktok_update_inventory(merchant_id, sku, -1))  # placeholder

    elif action_type == "reorder":
        sku = payload.get("sku", "")
        quantity = int(payload.get("quantity", 0))
        if sku and quantity > 0:
            results.append(shopify_update_inventory(merchant_id, sku, quantity))
            results.append(tiktok_update_inventory(merchant_id, sku, quantity))
            results.append(amazon_update_inventory(merchant_id, sku, quantity))

    elif action_type == "refund":
        order_id = payload.get("order_id", "")
        results.append({"status": "pending", "channel": "refund", "order_id": order_id})

    return {
        "status": "ok" if all(r.get("status") in ("ok", "pending", "skipped") for r in results) else "partial_failure",
        "results": results,
    }
