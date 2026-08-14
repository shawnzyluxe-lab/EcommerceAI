"""Live marketplace write-backs for approved actions.

Handles outbound inventory, price, and fulfillment updates to Shopify,
TikTok Shop, and Amazon where credentials are configured. Missing
configurations are logged rather than raising, so a local fallback record
is always created.
"""
import json
import logging
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any, Dict, List, Optional, Tuple

import requests

from models import db, GeneratedPurchaseOrder, MerchantProfile, MerchantSetting, OutboundTransmission, Product, Supplier
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
    """Push an inventory update to TikTok Shop, resolving SKU to product/sku_id."""
    return tiktok_sync.update_inventory(merchant_id, sku, quantity)


def amazon_update_inventory(merchant_id: str, sku: str, quantity: int) -> Dict[str, Any]:
    """Push an inventory update to Amazon via SP-API patchListingsItem."""
    return amazon_sync.update_inventory(merchant_id, sku, quantity)


def ad_platform_update_budget(platform: str, merchant_id: str, new_budget: float, campaign_id: Optional[str] = None) -> Dict[str, Any]:
    """Push an ad budget adjustment to the target platform.

    Currently records the target budget locally; live ad API integrations
    (Meta, Google, TikTok Ads) require separate OAuth scopes not yet wired.
    """
    logger.info(f"[Outbound] Ad budget update queued for {platform}: ${new_budget} (campaign={campaign_id})")
    return {"status": "pending", "channel": platform, "new_budget": float(new_budget), "campaign_id": campaign_id}


def _resolve_supplier_for_po(
    merchant_id: str,
    sku: str,
    fallback_supplier_name: str = "Supplier",
) -> Tuple[str, Optional[str]]:
    """Return the best supplier name and email for a purchase order."""
    product = Product.query.filter_by(sku=sku, merchant_id=merchant_id).first()
    if product and product.supplier_id:
        supplier = Supplier.query.filter_by(id=product.supplier_id, merchant_id=merchant_id).first()
        if supplier:
            return (supplier.name or fallback_supplier_name), supplier.email

    setting = MerchantSetting.query.filter_by(merchant_id=merchant_id, setting_key="supplier_email").first()
    if setting and setting.setting_value:
        return fallback_supplier_name, setting.setting_value.strip()

    env_email = os.environ.get("SUPPLIER_EMAIL", "")
    if env_email:
        return fallback_supplier_name, env_email

    return fallback_supplier_name, None


def _log_email(transmission_type: str, recipient: str, status: str, summary: str) -> None:
    """Persist an outbound email transmission record."""
    try:
        db.session.add(OutboundTransmission(
            transmission_type=transmission_type,
            recipient_address=recipient,
            status_chip=status,
            payload_summary=summary,
        ))
        db.session.commit()
    except Exception:
        logger.exception("[Outbound] Failed to log email transmission")


def _send_email(to: str, subject: str, html_body: str, bcc: Optional[str] = None, text_body: Optional[str] = None) -> bool:
    """Send a transactional email via Mailgun or SMTP fallback."""
    mailgun_key = os.environ.get("MAILGUN_API_KEY", "")
    mailgun_domain = os.environ.get("MAILGUN_DOMAIN", "")
    recipients = [to] + ([bcc] if bcc else [])

    if mailgun_key and mailgun_domain:
        try:
            data = {
                "from": f"Vantav <postmaster@{mailgun_domain}>",
                "to": to,
                "subject": subject,
                "html": html_body,
            }
            if bcc:
                data["bcc"] = bcc
            resp = requests.post(
                f"https://api.mailgun.net/v3/{mailgun_domain}/messages",
                auth=("api", mailgun_key),
                data=data,
                timeout=10,
            )
            if resp.status_code == 200:
                for r in recipients:
                    _log_email("EMAIL", r, "DELIVERED", subject)
                return True
            err = f"Mailgun {resp.status_code}: {resp.text[:500]}"
            for r in recipients:
                _log_email("EMAIL", r, "FAILED_ROUTING", err)
            return False
        except Exception as e:
            for r in recipients:
                _log_email("EMAIL", r, "FAILED_ROUTING", str(e))
            return False

    smtp_server = os.environ.get("SMTP_SERVER", "smtp.mailgun.org")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USERNAME", "")
    smtp_pass = os.environ.get("SMTP_PASSWORD", "")
    if not smtp_user or not smtp_pass:
        _log_email("EMAIL", to, "NO_CREDENTIALS", "No Mailgun or SMTP credentials configured")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Vantav <{smtp_user}>"
        msg["To"] = to
        if bcc:
            msg["Bcc"] = bcc
        if text_body:
            msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, recipients, msg.as_string())

        for r in recipients:
            _log_email("EMAIL", r, "DELIVERED", subject)
        return True
    except Exception as e:
        _log_email("EMAIL", to, "FAILED_ROUTING", str(e))
        return False


def send_supplier_po(
    merchant_id: str,
    po: GeneratedPurchaseOrder,
    supplier_name: Optional[str] = None,
    supplier_email: Optional[str] = None,
    lead_days: int = 7,
) -> Dict[str, Any]:
    """Transmit a purchase order to the supplier and notify the merchant."""
    profile = MerchantProfile.query.get(merchant_id)
    business_name = (profile.business_name or "Your Store") if profile else "Your Store"
    merchant_email = (profile.admin_email or "") if profile else ""

    resolved_name, resolved_email = _resolve_supplier_for_po(
        merchant_id, po.variant_sku, fallback_supplier_name=supplier_name or "Supplier"
    )
    supplier_name = supplier_name or resolved_name or "Supplier"
    supplier_email = supplier_email or resolved_email

    to_email = supplier_email or merchant_email
    bcc_email = merchant_email if to_email and to_email != merchant_email else None

    if not to_email:
        logger.warning(f"[Outbound] No supplier or merchant email for PO {po.po_reference}")
        return {
            "status": "pending",
            "po_reference": po.po_reference,
            "sku": po.variant_sku,
            "units": po.units_ordered,
            "reason": "No email recipient configured",
        }

    subject = f"Purchase Order {po.po_reference} — {business_name}"
    text_body = (
        f"Hi {supplier_name},\n\n"
        f"Please find a new purchase order from {business_name}:\n\n"
        f"PO Reference: {po.po_reference}\n"
        f"SKU: {po.variant_sku}\n"
        f"Quantity: {po.units_ordered}\n"
        f"Requested lead time: {lead_days} days\n\n"
        f"Please confirm receipt and estimated ship date.\n\n"
        f"Thank you,\nVantav on behalf of {business_name}"
    )
    html_body = f"""<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
  <p>Hi {supplier_name},</p>
  <p>Please find a new purchase order from <strong>{business_name}</strong>:</p>
  <table style="border-collapse: collapse; width: 100%; max-width: 500px;">
    <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>PO Reference</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{po.po_reference}</td></tr>
    <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>SKU</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{po.variant_sku}</td></tr>
    <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Quantity</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{po.units_ordered}</td></tr>
    <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Lead time</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{lead_days} days</td></tr>
  </table>
  <p>Please confirm receipt and estimated ship date.</p>
  <p>Thank you,<br>Vantav on behalf of {business_name}</p>
</body>
</html>"""

    sent = _send_email(to_email, subject, html_body, bcc=bcc_email, text_body=text_body)

    if sent:
        po.fulfillment_status = "PO_SENT"
        po.updated_at = datetime.utcnow()
        try:
            db.session.commit()
        except Exception:
            logger.exception("[Outbound] Failed to update PO status after email")

    return {
        "status": "ok" if sent else "failed",
        "po_reference": po.po_reference,
        "sku": po.variant_sku,
        "units": po.units_ordered,
        "to": to_email,
        "bcc": bcc_email,
        "supplier": supplier_name,
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
