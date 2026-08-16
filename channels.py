"""Channel connection manager — Shopify, TikTok Shop, Amazon SP-API, and manual API tokens."""
import base64
import json
import re
from datetime import datetime
from typing import Dict, Any, List, Optional

import requests

from models import db, MerchantChannel, TenantOAuthToken, CommerceChannel, ProfitFeedOrder, MerchantProfile, MerchantSetting
from tier_manager import TierManager


def _encode_token(raw: str) -> str:
    """Store tokens in a non-plaintext representation (base64 obfuscation)."""
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")


def _decode_token(stored: str) -> str:
    return base64.b64decode(stored.encode("utf-8")).decode("utf-8")


def _platform_color(platform: str) -> str:
    colors = {
        "shopify": "#7AB55C",
        "tiktok": "#000000",
        "amazon": "#FF9900",
        "ebay": "#E53238",
        "walmart": "#0071CE",
        "bigcommerce": "#34313F",
        "woocommerce": "#96588A",
    }
    return colors.get(platform, "#4A5C6A")


def _platform_abbr(platform: str) -> str:
    return {"shopify": "SP", "tiktok": "TT", "amazon": "AZ", "ebay": "EB", "walmart": "WM", "bigcommerce": "BC", "woocommerce": "WC"}.get(platform, platform[:2].upper())


def _platform_default_name(platform: str) -> str:
    base = {
        "shopify": "Shopify",
        "tiktok": "TikTok",
        "amazon": "Amazon",
        "ebay": "eBay",
        "walmart": "Walmart",
        "bigcommerce": "BigCommerce",
        "woocommerce": "WooCommerce",
    }.get(platform, platform.title())
    if platform == "tiktok":
        return base + " Shop"
    if platform == "amazon":
        return base + " Marketplace"
    return base


def list_channels(merchant_id: str) -> List[Dict[str, Any]]:
    """Return the canonical channel catalog with merchant-specific connection state."""
    if not merchant_id:
        return []
    connected = {mc.channel_id: mc for mc in MerchantChannel.query.filter_by(merchant_id=merchant_id).all()}
    tokens = {t.platform_id: t for t in TenantOAuthToken.query.filter_by(merchant_id=merchant_id).all()}

    # Aggregate recent revenue per channel so dashboard tables can render c.revenue.
    from sqlalchemy import func
    revenue_rows = dict(
        ProfitFeedOrder.query.filter_by(merchant_id=merchant_id)
        .with_entities(ProfitFeedOrder.channel, func.coalesce(func.sum(ProfitFeedOrder.gross_revenue), 0.0))
        .group_by(ProfitFeedOrder.channel)
        .all()
    )

    channels = []
    for platform in ["shopify", "tiktok", "amazon", "ebay", "walmart", "bigcommerce", "woocommerce"]:
        cc = CommerceChannel.query.get(platform)
        mc = connected.get(platform)
        token = tokens.get(platform)
        state = "connected" if (mc or token) else "disconnected"
        display_name_setting = MerchantSetting.query.get((merchant_id, f"channel_name:{platform}"))
        default_name = _platform_default_name(platform)
        display_name = display_name_setting.setting_value if display_name_setting else default_name
        channels.append({
            "platform": platform,
            "name": display_name,
            "default_name": default_name,
            "abbr": _platform_abbr(platform),
            "color": _platform_color(platform),
            "state": state,
            "orders": mc.pending_orders if mc else 0,
            "revenue": float(revenue_rows.get(platform, 0.0)),
            "conversion_rate": mc.conversion_rate if mc else 0.0,
            "sync": "Never" if state == "disconnected" else (token.updated_at.isoformat() if token else "now"),
        })
    return channels


def _ensure_commerce_channel(platform: str):
    if not CommerceChannel.query.get(platform):
        db.session.add(CommerceChannel(
            channel_id=platform,
            channel_name=(platform.title() + (" Shop" if platform in ("tiktok", "amazon") else "")),
            pending_orders=0,
            conversion_rate=3.5,
            performance_status="active",
        ))


def _enforce_store_limit(merchant_id: str) -> None:
    if not TierManager.can_add_store(merchant_id):
        profile = MerchantProfile.query.get(merchant_id)
        tier = profile.account_tier if profile else "Basic Tier"
        limit = TierManager.get_store_limit(tier)
        raise ValueError(f"Store connection limit reached. Upgrade your plan to connect more than {limit} stores.")


def connect_shopify(merchant_id: str, shop_domain: str, access_token: str) -> Dict[str, Any]:
    """Persist a Shopify store connection."""
    _enforce_store_limit(merchant_id)
    if not re.match(r'^[a-zA-Z0-9\-]+\.myshopify\.com$', shop_domain.lower()):
        raise ValueError("Invalid Shopify domain. Use store.myshopify.com")

    _ensure_commerce_channel("shopify")

    token = TenantOAuthToken.query.get(shop_domain.lower())
    if not token:
        token = TenantOAuthToken(shop_domain=shop_domain.lower(), merchant_id=merchant_id, platform_id="shopify")
        db.session.add(token)
    token.access_token_encrypted = _encode_token(access_token)
    token.scope_permissions = "read_products,write_products,read_orders,read_inventory,read_fulfillments"
    token.updated_at = datetime.utcnow()

    mc = MerchantChannel.query.filter_by(merchant_id=merchant_id, channel_id="shopify").first()
    if not mc:
        mc = MerchantChannel(merchant_id=merchant_id, channel_id="shopify", pending_orders=0, conversion_rate=3.5)
        db.session.add(mc)

    db.session.commit()
    return {"platform": "shopify", "state": "connected", "domain": shop_domain}


def connect_tiktok(
    merchant_id: str,
    seller_id: str,
    app_key: str,
    app_secret: str,
    access_token: str = "",
    shop_cipher: str = "",
    refresh_token: str = "",
    region: str = "",
) -> Dict[str, Any]:
    """Persist a TikTok Shop connection."""
    _enforce_store_limit(merchant_id)
    _ensure_commerce_channel("tiktok")
    account_id = f"tiktok:{seller_id}"
    token = TenantOAuthToken.query.get(account_id)
    if not token:
        token = TenantOAuthToken(shop_domain=account_id, merchant_id=merchant_id, platform_id="tiktok")
        db.session.add(token)
    token.access_token_encrypted = _encode_token(json.dumps({
        "app_key": app_key,
        "app_secret": app_secret,
        "seller_id": seller_id,
        "shop_id": seller_id,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "shop_cipher": shop_cipher,
        "region": region,
    }))
    token.scope_permissions = "shop.list,order.list,product.list"
    token.updated_at = datetime.utcnow()

    mc = MerchantChannel.query.filter_by(merchant_id=merchant_id, channel_id="tiktok").first()
    if not mc:
        mc = MerchantChannel(merchant_id=merchant_id, channel_id="tiktok", pending_orders=0, conversion_rate=3.5)
        db.session.add(mc)
    db.session.commit()
    return {"platform": "tiktok", "state": "connected", "seller_id": seller_id}


def connect_amazon(
    merchant_id: str,
    seller_id: str,
    access_key: str,
    secret_key: str,
    region: str,
    refresh_token: str = "",
    lwa_client_id: str = "",
    lwa_client_secret: str = "",
    role_arn: str = "",
) -> Dict[str, Any]:
    """Persist an Amazon SP-API connection."""
    _enforce_store_limit(merchant_id)
    _ensure_commerce_channel("amazon")
    account_id = f"amazon:{region}:{seller_id}"
    token = TenantOAuthToken.query.get(account_id)
    if not token:
        token = TenantOAuthToken(shop_domain=account_id, merchant_id=merchant_id, platform_id="amazon")
        db.session.add(token)
    token.access_token_encrypted = _encode_token(json.dumps({
        "access_key": access_key,
        "secret_key": secret_key,
        "region": region,
        "seller_id": seller_id,
        "refresh_token": refresh_token,
        "lwa_client_id": lwa_client_id,
        "lwa_client_secret": lwa_client_secret,
        "role_arn": role_arn,
    }))
    token.scope_permissions = "sellingpartnerapi::notifications"
    token.updated_at = datetime.utcnow()

    mc = MerchantChannel.query.filter_by(merchant_id=merchant_id, channel_id="amazon").first()
    if not mc:
        mc = MerchantChannel(merchant_id=merchant_id, channel_id="amazon", pending_orders=0, conversion_rate=3.5)
        db.session.add(mc)
    db.session.commit()
    return {"platform": "amazon", "state": "connected", "seller_id": seller_id, "region": region}


def disconnect(merchant_id: str, platform: str) -> Dict[str, Any]:
    """Remove merchant channel connection and stored tokens."""
    mc = MerchantChannel.query.filter_by(merchant_id=merchant_id, channel_id=platform).first()
    if mc:
        db.session.delete(mc)
    TenantOAuthToken.query.filter_by(merchant_id=merchant_id, platform_id=platform).delete()
    db.session.commit()
    return {"platform": platform, "state": "disconnected"}


def get_token(merchant_id: str, platform: str) -> Optional[str]:
    """Return decoded token/credential string for a merchant/platform."""
    token = TenantOAuthToken.query.filter_by(merchant_id=merchant_id, platform_id=platform).first()
    return _decode_token(token.access_token_encrypted) if token else None


def shopify_oauth_exchange(shop: str, code: str, client_id: str, client_secret: str) -> Dict[str, Any]:
    """Exchange Shopify OAuth code for an access token."""
    url = f"https://{shop}/admin/oauth/access_token"
    resp = requests.post(url, json={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
    }, timeout=20)
    if resp.status_code != 200:
        raise ValueError(f"Shopify token exchange failed: {resp.text}")
    data = resp.json()
    return {"access_token": data.get("access_token"), "scope": data.get("scope", "")}
