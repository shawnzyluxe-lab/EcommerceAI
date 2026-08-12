"""Amazon Selling Partner API sync for orders and product catalog."""
import csv
import io
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sp_api.api import Orders, Reports
from sp_api.base import Marketplaces, SellingApiException
from sp_api.base.reportTypes import ReportType

from models import db, MerchantChannel, TenantOAuthToken, MerchantSetting
import channels as channels_module
import profit_feed

logger = logging.getLogger(__name__)


def _get_credentials(merchant_id: str) -> Optional[Dict[str, str]]:
    token = TenantOAuthToken.query.filter_by(merchant_id=merchant_id, platform_id="amazon").first()
    if not token:
        return None
    try:
        creds = json.loads(channels_module.get_token(merchant_id, "amazon") or "{}")
    except json.JSONDecodeError:
        return None
    return creds


def _marketplace(region: str) -> Marketplaces:
    r = (region or "us-east-1").lower()
    if "us" in r:
        return Marketplaces.US
    if "gb" in r or "uk" in r:
        return Marketplaces.GB
    if "ca" in r:
        return Marketplaces.CA
    if "jp" in r:
        return Marketplaces.JP
    if "au" in r:
        return Marketplaces.AU
    if "mx" in r:
        return Marketplaces.MX
    if "br" in r:
        return Marketplaces.BR
    if "in" in r:
        return Marketplaces.IN
    if "de" in r or "eu" in r:
        return Marketplaces.DE
    return Marketplaces.US


def _sp_api_credentials(creds: Dict[str, str]) -> Dict[str, str]:
    """Map stored Amazon credentials to python-amazon-sp-api credential keys."""
    out = {
        "lwa_app_id": creds.get("lwa_client_id", ""),
        "lwa_client_secret": creds.get("lwa_client_secret", ""),
        "refresh_token": creds.get("refresh_token", ""),
        "aws_access_key": creds.get("access_key", ""),
        "aws_secret_key": creds.get("secret_key", ""),
    }
    if creds.get("role_arn"):
        out["role_arn"] = creds["role_arn"]
    return out


def _map_order_status(status: str) -> str:
    s = (status or "").upper()
    if s == "CANCELED":
        return "cancelled"
    if s in ("SHIPPED", "INVOICE_UNCONFIRMED"):
        return "shipped"
    if s in ("PENDING", "PENDING_AVAILABILITY", "UNSHIPPED", "PARTIALLY_SHIPPED"):
        return "packed"
    return "delayed"


def sync_orders(merchant_id: str) -> int:
    """Pull Amazon orders from SP-API and feed the profit engine."""
    creds = _get_credentials(merchant_id)
    if not creds:
        raise ValueError("Amazon is not connected for this merchant")

    credentials = _sp_api_credentials(creds)
    missing = [k for k, v in credentials.items() if not v and k in ("lwa_app_id", "lwa_client_secret", "refresh_token", "aws_access_key", "aws_secret_key")]
    if missing:
        raise ValueError(f"Amazon credentials incomplete: {', '.join(missing)}")

    region = creds.get("region", "us-east-1")
    marketplace = _marketplace(region)
    since = (datetime.utcnow() - timedelta(days=30)).isoformat()

    try:
        res = Orders(credentials=credentials, marketplace=marketplace).get_orders(CreatedAfter=since)
    except SellingApiException as e:
        raise ValueError(f"Amazon SP-API orders error: {e}")

    orders = (res.payload or {}).get("Orders", [])
    count = 0
    for order in orders:
        try:
            order_id = f"{merchant_id}:amazon:{order.get('AmazonOrderId')}"
            total = order.get("OrderTotal") or {}
            gross = float(total.get("Amount", 0.0) or 0.0)
            items = int(order.get("NumberOfItemsShipped", 0) or 0) + int(order.get("NumberOfItemsUnshipped", 0) or 0)
            if not items:
                items = 1
            state = _map_order_status(order.get("OrderStatus"))
            profit_feed.record_order(
                merchant_id=merchant_id,
                channel="amazon",
                order_id=order_id,
                gross_revenue=gross,
                items=items,
                state=state,
            )
            count += 1
        except Exception as e:
            logger.error(f"[Amazon Sync] Failed to import order {order.get('AmazonOrderId')}: {e}")

    mch = MerchantChannel.query.filter_by(merchant_id=merchant_id, channel_id="amazon").first()
    if mch:
        mch.pending_orders = max(0, len(orders) - count)
    tk = TenantOAuthToken.query.filter_by(merchant_id=merchant_id, platform_id="amazon").first()
    if tk:
        tk.updated_at = datetime.utcnow()
    db.session.commit()
    return count


def sync_products(merchant_id: str) -> int:
    """Request an Amazon All Listings report and store the catalog.

    This kicks off an async report, polls briefly, and falls back to any
    previously cached catalog if the report is not yet ready.
    """
    creds = _get_credentials(merchant_id)
    if not creds:
        raise ValueError("Amazon is not connected for this merchant")

    credentials = _sp_api_credentials(creds)
    missing = [k for k, v in credentials.items() if not v and k in ("lwa_app_id", "lwa_client_secret", "refresh_token", "aws_access_key", "aws_secret_key")]
    if missing:
        raise ValueError(f"Amazon credentials incomplete: {', '.join(missing)}")

    region = creds.get("region", "us-east-1")
    marketplace = _marketplace(region)
    marketplace_id = marketplace.marketplace_id

    try:
        report_res = Reports(credentials=credentials, marketplace=marketplace).create_report(
            reportType=ReportType.GET_MERCHANT_LISTINGS_ALL_DATA,
            marketplaceIds=[marketplace_id],
        )
        report_id = report_res.payload["reportId"]
    except SellingApiException as e:
        raise ValueError(f"Amazon SP-API report creation error: {e}")

    report_doc_id = None
    for _ in range(6):
        try:
            status_res = Reports(credentials=credentials, marketplace=marketplace).get_report(report_id)
            status = status_res.payload.get("processingStatus")
            if status == "DONE":
                report_doc_id = status_res.payload.get("reportDocumentId")
                break
            if status in ("CANCELLED", "FATAL"):
                raise ValueError(f"Amazon report processing failed: {status}")
        except SellingApiException as e:
            logger.warning(f"[Amazon Sync] Report poll error: {e}")
        time.sleep(3)

    catalog = []
    if report_doc_id:
        try:
            doc_res = Reports(credentials=credentials, marketplace=marketplace).get_report_document(
                report_doc_id, download=True
            )
            content = doc_res.payload
            if isinstance(content, bytes):
                text = content.decode("utf-8", errors="replace")
            elif isinstance(content, str):
                text = content
            elif isinstance(content, dict) and "document" in content:
                text = content["document"]
            else:
                text = str(content)
            reader = csv.DictReader(io.StringIO(text), delimiter="\t")
            for row in reader:
                try:
                    price = float(row.get("price", 0.0) or 0.0)
                    qty = int(float(row.get("quantity", 0) or 0))
                    product_id = row.get("asin1") or row.get("asin") or row.get("product-id") or ""
                    sku = row.get("seller-sku") or ""
                    catalog.append({
                        "product_id": product_id,
                        "variant_id": sku,
                        "title": row.get("item-name", ""),
                        "sku": sku,
                        "price": price,
                        "inventory_quantity": qty,
                        "image_url": row.get("image-url", ""),
                        "status": row.get("status", ""),
                        "product_type": "",
                    })
                except (TypeError, ValueError) as e:
                    logger.warning(f"[Amazon Sync] Skipping malformed listing row: {e}")
        except SellingApiException as e:
            logger.error(f"[Amazon Sync] Report document error: {e}")

    setting = MerchantSetting.query.filter_by(merchant_id=merchant_id, setting_key="amazon_products").first()
    if not setting:
        setting = MerchantSetting(merchant_id=merchant_id, setting_key="amazon_products")
        db.session.add(setting)
    setting.setting_value = json.dumps(catalog)
    db.session.commit()
    return len(catalog)


def sync_amazon(merchant_id: str) -> Dict[str, Any]:
    """One-call sync of Amazon orders and product catalog."""
    order_count = sync_orders(merchant_id)
    product_count = sync_products(merchant_id)
    return {"orders_synced": order_count, "products_synced": product_count}


def get_products(merchant_id: str) -> List[Dict[str, Any]]:
    """Return the last-synced Amazon product catalog for a merchant."""
    setting = MerchantSetting.query.filter_by(merchant_id=merchant_id, setting_key="amazon_products").first()
    if not setting or not setting.setting_value:
        return []
    try:
        return json.loads(setting.setting_value)
    except json.JSONDecodeError:
        return []
