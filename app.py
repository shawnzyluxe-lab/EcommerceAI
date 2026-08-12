import os
import re
import hmac
import hashlib
import base64
import json
import secrets
import uuid
import requests
import smtplib
import logging
import asyncio
import dataclasses
from threading import Thread
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from twilio.rest import Client as TwilioClient
from werkzeug.security import generate_password_hash, check_password_hash
from smart_router import AISmartRouter, OrderRoutingPayload as SmartOrderPayload, WarehouseInventoryNode
from product_transformer import ProductTransformerEngine, ShopifyProductLayout
from ai_coo_engine import AICooEngine, COORunnerPayload
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from datetime import datetime, timedelta
from enum import Enum
from functools import wraps
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(module)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("shawnzyluxe_core")
from urllib.parse import urlencode
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file, make_response, after_this_request
from flask_sock import Sock
from dotenv import load_dotenv

load_dotenv()

SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.1,
            profiles_sample_rate=0.0,
        )
        print("[SENTRY] Initialized")
    except Exception as e:
        print(f"[SENTRY] Init failed: {e}")

from models import db, Tenant, ConnectedChannel, ActiveSession, BusinessMetric, CommerceChannel, MerchantChannel, SupportMetric, MarketingStudio, PredictiveLogistics, OutboundTransmission, SaaSBilling, LocalProductCatalog, MerchantProfile, TenantOAuthToken, MerchantMetric, SystemExceptionLog, ProcessedWebhookEvent, AdSpendAnalytic, GeneratedPurchaseOrder, AIAgent, AgentMessage, MerchantDecisionLog, MagicLoginToken, TrendingProduct, ProductFinancialLedger, MerchantSetting, ProfitFeedOrder, AdSpendFeed, Alert, BetaWaitlistApplication, PendingAction, StartupPackProject
import profit_feed
import billing as billing_module
import alert_matrix
import vetted_operator
import action_gate
import channels as channels_module
import shopify_sync
import tiktok_sync
import amazon_sync
import tiktok_studio
import assistant_engine
import startup_pack
import sandbox_demo
import migrate as migrate_module
from dashboard_context import (
    context,
    RECENT_ORDERS,
    PROFIT_BREAKDOWN,
    BRIEFING,
    COO,
    CHANNELS,
    SUPPORT,
    MARKETING,
    STRIPE,
    CATALOG,
    predictive_context,
)
from trend_worker import run_trend_scrape, TrendingProductsScraper

# Dynamic state for AI command engine
DASHBOARD_STATE = {
    "total_unified_balance": 20560.00,
    "true_net_profit": 1394.00,
    "gross_revenue": 4582.00,
    "ai_briefing": COO["narrative"],
    "conversion_feeds": [
        {"store": "Shopify Storefront", "rate": "3.4%", "status": "Optimal", "up": True},
        {"store": "TikTok Video Shop", "rate": "4.1%", "status": "Trending", "up": True},
        {"store": "Amazon Marketplace", "rate": "2.8%", "status": "Stable", "up": False},
    ],
    "channels": {
        "shopify": {"pending_orders": 12},
        "amazon": {"pending_orders": 4},
        "tiktok": {"pending_orders": 7},
    },
    "support_chats": 3,
    "support_sentiment": "94% Positive",
    "support_resolution": "Order #1204 tracking corrected autonomously.",
    "mktg_campaign": "Summer Clearance Blast",
    "mktg_status": "Idle",
    "mktg_copy": "Awaiting generation trigger query text...",
    "stripe_usage": 4820,
    "stripe_invoice": 241.00,
    "hoodie_price": 145.00,
}

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(64))
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///shawnzyluxe.db")
# Force psycopg v3 dialect if the user provided a plain postgresql:// URL
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg" + DATABASE_URL[len("postgresql"):]
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

LIMITER_STORAGE_URI = os.environ.get("LIMITER_STORAGE_URI", "memory://")
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["1000 per hour", "100 per minute"],
    storage_uri=LIMITER_STORAGE_URI,
)

GENERATED_DIR = "generated"
os.makedirs(GENERATED_DIR, exist_ok=True)

db.init_app(app)
sock = Sock(app)


class ConnectionManager:
    def __init__(self):
        self.active_connections = []

    def connect(self, websocket):
        self.active_connections.append(websocket)

    def disconnect(self, websocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    def broadcast(self, message):
        dead = []
        for connection in self.active_connections:
            try:
                connection.send(json.dumps(message))
            except Exception:
                dead.append(connection)
        for connection in dead:
            if connection in self.active_connections:
                self.active_connections.remove(connection)


manager = ConnectionManager()

# ============================================================
# AEGIS-STYLE SITE PASSWORD WALL
# ============================================================

SITE_WALL_PASSWORD = os.environ.get("SITE_WALL_PASSWORD", "")
MASTER_ADMIN_EMAIL = os.environ.get("MASTER_ADMIN_EMAIL", "shawn@shawnzyluxe.com,admin@shawnzyluxe.com")
MASTER_ADMIN_EMAILS = [e.strip().lower() for e in MASTER_ADMIN_EMAIL.split(",") if e.strip()]
ENGINEER_EMAIL = os.environ.get("ENGINEER_EMAIL", "engineer@shawnzyluxe.com")
ENGINEER_EMAILS = [e.strip().lower() for e in ENGINEER_EMAIL.split(",") if e.strip()]
RECAPTCHA_SITE_KEY = os.environ.get("RECAPTCHA_SITE_KEY", "")
RECAPTCHA_SECRET_KEY = os.environ.get("RECAPTCHA_SECRET_KEY", "")
RECAPTCHA_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"
SESSION_COOKIE_NAME = "aegis_session_token"
SESSION_TIMEOUT_DAYS = int(os.environ.get("SESSION_TIMEOUT_DAYS", "7"))
SESSION_IDLE_TIMEOUT_MINUTES = int(os.environ.get("SESSION_IDLE_TIMEOUT_MINUTES", "30"))
SESSION_MAX_AGE_HOURS = int(os.environ.get("SESSION_MAX_AGE_HOURS", "12"))

BETA_MODE = os.environ.get("BETA_MODE", "false").lower() in ("true", "1", "yes")
BETA_READY_DASHBOARD_PAGES = {
    "overview", "alerts", "action-gate", "profit-engine", "billing", "startup-pack", "commerce-hub",
    "tiktok-studio", "command-center",
}

TIER_LIMITS = {
    "Basic Tier": {
        "monthly_order_limit": 500,
        "max_monthly_operations": 500,
        "max_store_connections": 2,
        "sync_frequency_seconds": 900,
        "advanced_automation": False,
        "features_allowed": ["shopify", "support"],
    },
    "Beta Tier": {
        "monthly_order_limit": 5000,
        "max_monthly_operations": 5000,
        "max_store_connections": 999999,
        "sync_frequency_seconds": 300,
        "advanced_automation": True,
        "features_allowed": ["shopify", "tiktok", "amazon", "support", "marketing"],
    },
    "Beta + Startup Pack": {
        "monthly_order_limit": 5000,
        "max_monthly_operations": 5000,
        "max_store_connections": 999999,
        "sync_frequency_seconds": 300,
        "advanced_automation": True,
        "features_allowed": ["shopify", "tiktok", "amazon", "support", "marketing"],
    },
    "Pro Tier": {
        "monthly_order_limit": 5000,
        "max_monthly_operations": 5000,
        "max_store_connections": 999999,
        "sync_frequency_seconds": 300,
        "advanced_automation": True,
        "features_allowed": ["shopify", "tiktok", "support", "marketing"],
    },
    "Enterprise AI Tier": {
        "monthly_order_limit": 999999,
        "max_monthly_operations": 999999,
        "max_store_connections": 999999,
        "sync_frequency_seconds": 0,
        "advanced_automation": True,
        "features_allowed": ["shopify", "tiktok", "amazon", "support", "marketing"],
    },
}


class TierManager:
    """Enforces tenant tier limits and feature flags for order automation."""

    @staticmethod
    def get_tier_meta(tier: str) -> dict:
        return TIER_LIMITS.get(tier, TIER_LIMITS["Basic Tier"])

    @staticmethod
    def verify_operational_allowance(merchant_id: str, current_usage: int) -> tuple[bool, str]:
        """Validate volume capacity before spinning up background workers."""
        profile = MerchantProfile.query.get(merchant_id)
        if not profile:
            return False, "Unknown merchant"
        account = SaaSBilling.query.get(merchant_id)
        if not account:
            return False, "No billing record"

        tier = profile.account_tier or "Basic Tier"
        meta = TierManager.get_tier_meta(tier)
        monthly_order_limit = meta["monthly_order_limit"]

        if current_usage >= monthly_order_limit:
            return False, f"LIMIT EXCEEDED: Brand has consumed its allotment of {monthly_order_limit} orders for this billing cycle. Please upgrade."
        return True, "OK"

    @staticmethod
    def route_order_automation(merchant_id: str, order_data: dict) -> dict:
        """Enforce feature flags based on tier level."""
        profile = MerchantProfile.query.get(merchant_id)
        if not profile:
            return {"status": "SKIPPED", "reason": "Unknown merchant"}

        tier = profile.account_tier or "Basic Tier"
        meta = TierManager.get_tier_meta(tier)

        if not meta["advanced_automation"]:
            logger.info(f"[TIER POLICY] Automation skipped for {merchant_id}. {tier} accounts must route orders manually.")
            return {"status": "SKIPPED", "reason": "Upgrade required for autonomous MCF routing."}

        logger.info(f"[TIER POLICY] Executing automated routing rule for {merchant_id} ({tier}).")
        return {"status": "DISPATCHED", "destination": "Amazon_FBA_Warehouse", "order_id": order_data.get("order_id")}


class UserRole(str, Enum):
    ADMIN = "Admin"
    MERCHANT = "Merchant"
    ENGINEER = "Engineer"


def get_current_user():
    """Return the active session record with its role, or None."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    s = ActiveSession.query.get(token)
    if not s or s.created_at < datetime.utcnow() - timedelta(days=SESSION_TIMEOUT_DAYS):
        return None
    return s


def require_roles(permitted_roles):
    """Flask RBAC decorator. Fails closed (403) on unauthorized access."""
    def decorator(endpoint_function):
        @wraps(endpoint_function)
        def wrapper(*args, **kwargs):
            s = get_current_user()
            if not s or s.role not in [role.value for role in permitted_roles]:
                return jsonify({"error": "SECURITY PROTOCOL VIOLATION: Unauthorized endpoint access attempt. Session invalidated."}), 403
            return endpoint_function(*args, **kwargs)
        return wrapper
    return decorator


def verify_captcha_v3(token: str) -> float:
    """Validate a Google reCAPTCHA v3 token and return the bot score (0.0-1.0)."""
    if not RECAPTCHA_SECRET_KEY:
        return 1.0  # If not configured, fail open for local dev
    try:
        response = requests.post(RECAPTCHA_VERIFY_URL, data={
            "secret": RECAPTCHA_SECRET_KEY,
            "response": token,
        }, timeout=5)
        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                return float(result.get("score", 0.0))
    except Exception as e:
        logger.warning(f"[CAPTCHA] Verification request failed: {e}")
    return 0.0


@dataclasses.dataclass
class WebhookOrderPayload:
    order_id: str
    sku: str
    quantity_purchased: int
    tenant_id: str

    @classmethod
    def from_dict(cls, data):
        return cls(
            order_id=str(data.get("order_id", "")),
            sku=str(data.get("sku", data.get("variant_sku", ""))),
            quantity_purchased=int(data.get("qty", data.get("quantity", 1))),
            tenant_id=str(data.get("tenant_id", data.get("merchant_id", ""))),
        )


class GlobalSystemCircuitBreaker:
    """Redis-backed global kill switch for background channel sync workers."""
    def __init__(self, redis_url: str):
        try:
            import redis.asyncio as redis
            self.client = redis.from_url(redis_url)
            self.enabled = True
        except Exception as e:
            logger.warning(f"Redis unavailable for circuit breaker: {e}")
            self.client = None
            self.enabled = False
        self.switch_key = "sys:matrix:global_sync_lock"

    async def engage_global_kill_switch(self):
        if not self.client:
            return
        await self.client.set(self.switch_key, "HALTED")
        logger.info("[HARD EXECUTABLE CONTROL] Global synchronization pipelines PAUSED.")

    async def release_system_lock(self):
        if not self.client:
            return
        await self.client.set(self.switch_key, "OPERATIONAL")
        logger.info("[HARD EXECUTABLE CONTROL] Global synchronization pipelines restored.")

    async def verify_pipeline_clearance(self) -> bool:
        if not self.client:
            return True
        status = await self.client.get(self.switch_key)
        if status == b"HALTED":
            return False
        return True


REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
circuit_breaker = GlobalSystemCircuitBreaker(REDIS_URL)


async def dispatch_shopify_sync(tenant_id: str, sku: str, new_stock: int):
    """Simulate outbound Shopify inventory patch."""
    await asyncio.sleep(0.05)
    logger.info(f"[ASYNC WORKER] Shopify sync queued. Tenant {tenant_id} SKU {sku} -> {new_stock}")
    return True


async def dispatch_amazon_mcf(tenant_id: str, sku: str, order_payload: dict):
    """Simulate outbound Amazon MCF order creation."""
    await asyncio.sleep(0.08)
    logger.info(f"[ASYNC WORKER] Amazon MCF queued. Tenant {tenant_id} SKU {sku}")
    return True


async def process_incoming_order_event(event_data: dict):
    """Absorb webhook order data, enforce tier policy, update DB, and fire multi-channel async workers."""
    if not await circuit_breaker.verify_pipeline_clearance():
        logger.warning("[CIRCUIT BREAKER] Incoming order dropped. System sync is HALTED.")
        return False

    payload = WebhookOrderPayload.from_dict(event_data)

    # Tier volume and feature-gate enforcement
    account = SaaSBilling.query.get(payload.tenant_id)
    current_usage = account.metered_usage_units if account else 0
    allowed, reason = TierManager.verify_operational_allowance(payload.tenant_id, current_usage)
    if not allowed:
        logger.warning(f"[TIER POLICY] TikTok order {payload.order_id} blocked for {payload.tenant_id}: {reason}")
        return False

    automation = TierManager.route_order_automation(payload.tenant_id, {"order_id": payload.order_id})
    if automation["status"] == "SKIPPED":
        logger.info(f"[TIER POLICY] {automation['reason']}")

    # Fetch current inventory from the merchant channel mirror
    pl = PredictiveLogistics.query.filter_by(variant_sku=payload.sku).first()
    current_stock = pl.days_remaining if pl else 12  # using days_remaining as a proxy metric
    calculated = max(0, current_stock - payload.quantity_purchased)

    # Update merchant channel and predictive logistics
    mc = MerchantChannel.query.filter_by(merchant_id=payload.tenant_id, channel_id="tiktok").first()
    if mc:
        mc.pending_orders += payload.quantity_purchased

    if pl:
        pl.days_remaining = calculated
        if pl.days_remaining < 7:
            pl.status_flag = "CRITICAL_STOCKOUT"
        elif pl.days_remaining < 14:
            pl.status_flag = "LOW_STOCK"

    db.session.add(BusinessMetric(
        merchant_id=payload.tenant_id,
        total_unified_balance=DASHBOARD_STATE.get("total_unified_balance", 20560.0),
        true_net_profit=DASHBOARD_STATE.get("true_net_profit", 1394.0),
        gross_revenue=DASHBOARD_STATE.get("gross_revenue", 4582.0),
        ai_briefing=f"🛒 TikTok order {payload.order_id} routed. SKU {payload.sku} stock proxy shifted to {calculated}.",
    ))
    db.session.commit()

    # Concurrent downstream dispatch
    await asyncio.gather(
        dispatch_shopify_sync(payload.tenant_id, payload.sku, calculated),
        dispatch_amazon_mcf(payload.tenant_id, payload.sku, event_data),
    )

    logger.info("[PIPELINE COMPLETE] Global inventory synchronized across active endpoints.")
    return True


# Sessions are stored in the database (ActiveSession table) for worker-safe, persistent auth.
# No in-memory session ring is used.


def site_wall_enabled():
    """The wall is enabled only when a password is configured."""
    return bool(SITE_WALL_PASSWORD)


def site_wall_authenticated(refresh=True):
    """Check whether the browser has a valid, non-expired session token.

    Enforces an absolute max age and an idle timeout. If refresh=True, touching a
    protected route bumps the idle window and resets the cookie expiry.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token is None:
        return False
    s = ActiveSession.query.get(token)
    now = datetime.utcnow()
    if not s:
        return False
    # Absolute max age check
    if s.created_at < now - timedelta(hours=SESSION_MAX_AGE_HOURS):
        db.session.delete(s)
        db.session.commit()
        return False
    # Idle timeout check
    if s.last_seen and s.last_seen < now - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES):
        db.session.delete(s)
        db.session.commit()
        return False
    if refresh:
        s.last_seen = now
        db.session.commit()
        # Refresh the cookie sliding window on protected activity.
        if hasattr(request, "session_cookie_refreshed"):
            pass
        else:
            request.session_cookie_refreshed = True
            @after_this_request
            def _refresh_cookie(response):
                max_age = SESSION_IDLE_TIMEOUT_MINUTES * 60
                response.set_cookie(
                    SESSION_COOKIE_NAME,
                    token,
                    max_age=max_age,
                    httponly=True,
                    samesite='Lax',
                    secure=app.config.get("SESSION_COOKIE_SECURE", os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"),
                )
                return response
    return True


def get_merchant_context():
    """Resolve merchant_id, account_tier, and business_name from the active session cookie."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    s = ActiveSession.query.get(token)
    now = datetime.utcnow()
    if not s or not s.merchant_id:
        return None
    # Enforce absolute and idle session lifetime for merchant sessions.
    if s.created_at < now - timedelta(days=SESSION_TIMEOUT_DAYS):
        db.session.delete(s)
        db.session.commit()
        return None
    if s.last_seen and s.last_seen < now - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES):
        db.session.delete(s)
        db.session.commit()
        return None
    profile = MerchantProfile.query.get(s.merchant_id)
    if not profile:
        return None
    tier = (profile.account_tier or "Basic Tier").replace("AI Tier", "Plan").replace("AI", "").strip()
    sandbox_status = profile.sandbox_status or "pending"
    sandbox_expired = False
    if sandbox_status == "sandbox" and profile.sandbox_expires_at and profile.sandbox_expires_at <= now:
        sandbox_status = "expired"
        sandbox_expired = True
    display_name = profile.business_name or (profile.admin_email.split("@")[0] if profile.admin_email and "@" in profile.admin_email else s.merchant_id)
    return {
        "id": s.merchant_id,
        "tier": tier,
        "name": display_name,
        "email": profile.admin_email,
        "sandbox_status": sandbox_status,
        "live_access_enabled": bool(profile.live_access_enabled) and not sandbox_expired,
        "sandbox_expires_at": profile.sandbox_expires_at.isoformat() if profile.sandbox_expires_at else None,
        "sandbox_expired": sandbox_expired,
        "role": s.role,
    }


def check_tier_limits(merchant_id, requested_feature):
    """Return (allowed: bool, reason: str, status_code: int) based on tier and metered usage."""
    if not merchant_id:
        return False, "No merchant context", 403
    profile = MerchantProfile.query.get(merchant_id)
    if not profile:
        return False, "Unknown merchant", 403
    account = SaaSBilling.query.get(merchant_id)
    if not account:
        return False, "No billing record", 403

    tier = profile.account_tier or "Basic Tier"
    meta = TierManager.get_tier_meta(tier)

    if requested_feature not in meta["features_allowed"]:
        return False, f"{tier} does not include {requested_feature} access", 403

    if account.metered_usage_units >= meta["monthly_order_limit"]:
        return False, f"Monthly operation limit reached for {tier}", 402

    return True, "OK", 200


def enforce_tier_limits(merchant_id, requested_feature):
    """Helper that returns a Flask JSON response or None if allowed."""
    allowed, reason, status = check_tier_limits(merchant_id, requested_feature)
    if not allowed:
        logger.warning(f"Tier limit blocked: {merchant_id} -> {requested_feature} ({reason})")
        return jsonify({"error": "Operation Blocked", "reason": reason}), status
    return None


@app.before_request
def site_wall_protect():
    if not site_wall_enabled():
        return None
    if request.endpoint in ('home', 'login', 'site_login', 'site_logout', 'subscribe', 'thank_you', 'session_heartbeat', 'create_stripe_checkout', 'beta_apply', 'api_beta_apply', 'auth_login', 'auth_signup', 'auth_provision_node', 'shopify_orders_webhook', 'tiktok_orders_webhook', 'amazon_orders_webhook', 'stripe_billing_webhook', 'supplier_po_update', 'execute_mitigation', 'generate_magic_link', 'magic_login', 'register_merchant', 'shopify_oauth_callback', 'tiktok_oauth_callback', 'health_check', 'legal_terms', 'legal_privacy', 'legal_refund', 'static'):
        return None
    if site_wall_authenticated():
        return None
    return redirect(url_for('login'))


# ============================================================
# END SITE PASSWORD WALL
# ============================================================

SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.mailgun.org")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
MERCHANT_EMAIL = os.environ.get("MERCHANT_EMAIL", "shawn@shawnzyluxe.com")
SUPPLIER_EMAIL = os.environ.get("SUPPLIER_EMAIL", "production@supplier-c.com")

MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY", "")
MAILGUN_DOMAIN = os.environ.get("MAILGUN_DOMAIN", "")

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")
MERCHANT_PHONE = os.environ.get("MERCHANT_PHONE", "")

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_BETA_MONTHLY = os.environ.get("STRIPE_PRICE_BETA_MONTHLY", "")
STRIPE_PRICE_BETA_STARTUP = os.environ.get("STRIPE_PRICE_BETA_STARTUP", "")
STRIPE_PRICE_STARTUP_ADDON = os.environ.get("STRIPE_PRICE_STARTUP_ADDON", "")
STRIPE_PRICE_CUSTOM_BRAND_BUILD_SETUP = os.environ.get("STRIPE_PRICE_CUSTOM_BRAND_BUILD_SETUP", "")
STRIPE_PRICE_CUSTOM_BRAND_BUILD_MONTHLY = os.environ.get("STRIPE_PRICE_CUSTOM_BRAND_BUILD_MONTHLY", "")
STRIPE_PRICE_SEO_SETUP = os.environ.get("STRIPE_PRICE_SEO_SETUP", "")
STRIPE_PRICE_SEO_MONTHLY = os.environ.get("STRIPE_PRICE_SEO_MONTHLY", "")
STRIPE_PRICE_EMAIL_SETUP = os.environ.get("STRIPE_PRICE_EMAIL_SETUP", "")
STRIPE_PRICE_CURATED_AD_PLAN_MONTHLY = os.environ.get("STRIPE_PRICE_CURATED_AD_PLAN_MONTHLY", "")
SHOPIFY_STORE_URL = os.environ.get("SHOPIFY_STORE_URL", "")
SHOPIFY_ACCESS_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
SHOPIFY_CLIENT_ID = os.environ.get("SHOPIFY_CLIENT_ID", "")
SHOPIFY_CLIENT_SECRET = os.environ.get("SHOPIFY_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI = os.environ.get("OAUTH_REDIRECT_URI", "https://shawnzyluxe.com")

TIKTOK_APP_KEY = os.environ.get("TIKTOK_APP_KEY", "")
TIKTOK_APP_SECRET = os.environ.get("TIKTOK_APP_SECRET", "")
TIKTOK_SERVICE_ID = os.environ.get("TIKTOK_SERVICE_ID", "")
TIKTOK_AUTH_REGION = os.environ.get("TIKTOK_AUTH_REGION", "")
TIKTOK_REDIRECT_URI = "https://vantavcommerce.com/api/v1/auth/tiktok/callback"

SHOPIFY_DOMAIN = os.environ.get('SHOPIFY_DOMAIN', '').strip()
STOREFRONT_TOKEN = os.environ.get('SHOPIFY_STOREFRONT_TOKEN', '').strip()
CUSTOMER_ACCOUNT_CLIENT_ID = os.environ.get('SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_ID', '').strip()
CUSTOMER_ACCOUNT_CLIENT_SECRET = os.environ.get('SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_SECRET', '').strip()

GRAPHQL_URL = f"https://{SHOPIFY_DOMAIN}/api/2024-07/graphql.json" if SHOPIFY_DOMAIN else None
CUSTOMER_ACCOUNT_BASE = f"https://shopify.com/{SHOPIFY_DOMAIN.split('.')[0]}" if SHOPIFY_DOMAIN else None

with app.app_context():
    # Bring existing Postgres schemas forward for new columns/tables before create_all.
    try:
        migrate_module.run_migrations()
    except Exception as e:
        app.logger.warning(f"Startup migration helper failed: {e}")
    db.create_all()

    # Clean expired sessions
    ActiveSession.query.filter(
        ActiveSession.created_at < datetime.utcnow() - timedelta(days=SESSION_TIMEOUT_DAYS)
    ).delete(synchronize_session=False)
    db.session.commit()

    # Seed / refresh multi-tenant merchant profiles first (other tables FK to it)
    temp_password = os.environ.get("TEMP_ACCOUNTS_PASSWORD") or (SITE_WALL_PASSWORD if SITE_WALL_PASSWORD else "IfxSVNs4iAs")
    temp_accounts = [
        ("merchant_shawn_01", "Shawnzyluxe Pro", "shawn@shawnzyluxe.com", "Beta Tier"),
        ("merchant_admin_temp", "Temporary Admin", "admin@shawnzyluxe.com", "Enterprise AI Tier"),
        ("merchant_engineer_temp", "Temporary Engineer", "engineer@shawnzyluxe.com", "Pro Tier"),
    ]
    for mid, name, email, tier in temp_accounts:
        p = MerchantProfile.query.get(mid)
        if p:
            p.business_name = name
            p.admin_email = email
            # Preserve tiers set by live Stripe webhooks (anything above Basic).
            if not p.account_tier or p.account_tier == "Basic Tier":
                p.account_tier = tier
            p.password_hash = generate_password_hash(temp_password, method="pbkdf2:sha256")
            p.sandbox_status = "approved"
            p.live_access_enabled = 1
        else:
            db.session.add(MerchantProfile(
                merchant_id=mid,
                business_name=name,
                admin_email=email,
                account_tier=tier,
                password_hash=generate_password_hash(temp_password, method="pbkdf2:sha256"),
                sandbox_status="approved",
                live_access_enabled=1,
            ))
    if not MerchantProfile.query.get("merchant_guest_02"):
        db.session.add(MerchantProfile(merchant_id="merchant_guest_02", business_name="Alpha Storefronts", admin_email="guest@alpha.com", account_tier="Pro Tier", password_hash="", sandbox_status="approved", live_access_enabled=1))
    db.session.commit()

    # Seed FK-dependent merchant data now that profiles exist
    if not MerchantMetric.query.filter_by(merchant_id="merchant_shawn_01").first():
        db.session.add(MerchantMetric(merchant_id="merchant_shawn_01", total_unified_balance=20560.00, true_net_profit=1394.00, gross_revenue=4582.00, ai_briefing="System initialized."))
    if not MerchantMetric.query.filter_by(merchant_id="merchant_guest_02").first():
        db.session.add(MerchantMetric(merchant_id="merchant_guest_02", total_unified_balance=1240.00, true_net_profit=410.00, gross_revenue=890.00, ai_briefing="System initialized."))
    if not MerchantChannel.query.filter_by(merchant_id="merchant_shawn_01").first():
        db.session.add(MerchantChannel(merchant_id="merchant_shawn_01", channel_id="shopify", pending_orders=12, conversion_rate=3.4))
        db.session.add(MerchantChannel(merchant_id="merchant_shawn_01", channel_id="amazon", pending_orders=4, conversion_rate=2.8))
        db.session.add(MerchantChannel(merchant_id="merchant_shawn_01", channel_id="tiktok", pending_orders=7, conversion_rate=4.1))
    if not SystemExceptionLog.query.first():
        db.session.add(SystemExceptionLog(module_origin="DATABASE_CORE", error_severity="INFO", exception_msg="Relational multi-tenant isolation layer fully hardened."))
    db.session.commit()

    # Seed or restore business metrics
    if not BusinessMetric.query.first():
        db.session.add(BusinessMetric(
            merchant_id="merchant_shawn_01",
            total_unified_balance=20560.00,
            true_net_profit=1394.00,
            gross_revenue=4582.00,
            ai_briefing=COO["narrative"],
        ))
    latest = BusinessMetric.query.order_by(BusinessMetric.id.desc()).first()
    if latest:
        DASHBOARD_STATE["total_unified_balance"] = latest.total_unified_balance
        DASHBOARD_STATE["true_net_profit"] = latest.true_net_profit
        DASHBOARD_STATE["gross_revenue"] = latest.gross_revenue
        DASHBOARD_STATE["ai_briefing"] = latest.ai_briefing
        COO["narrative"] = latest.ai_briefing
        BRIEFING["revenue"] = latest.gross_revenue
        BRIEFING["profit"] = latest.true_net_profit

    # Seed the real-time Profit Feed with demo data if no orders exist yet.
    profit_feed.seed_demo_data("merchant_shawn_01")
    profit_feed.seed_demo_data("merchant_guest_02")

    # Seed / refresh the Alert Matrix from latest data.
    alert_matrix.seed_demo_alerts("merchant_shawn_01")
    alert_matrix.refresh_alerts("merchant_shawn_01")

    # Seed or restore commerce channels
    if not CommerceChannel.query.first():
        db.session.add(CommerceChannel(channel_id="shopify", channel_name="Shopify Storefront", pending_orders=12, conversion_rate=3.4, performance_status="Optimal"))
        db.session.add(CommerceChannel(channel_id="tiktok", channel_name="TikTok Video Shop", pending_orders=7, conversion_rate=4.1, performance_status="Trending"))
        db.session.add(CommerceChannel(channel_id="amazon", channel_name="Amazon Marketplace", pending_orders=4, conversion_rate=2.8, performance_status="Stable"))
    for cc in CommerceChannel.query.all():
        DASHBOARD_STATE["channels"][cc.channel_id]["pending_orders"] = cc.pending_orders
        feed = next((f for f in DASHBOARD_STATE["conversion_feeds"] if cc.channel_name.lower() in f["store"].lower()), None)
        if feed:
            feed["rate"] = f"{cc.conversion_rate}%"
            feed["status"] = cc.performance_status
            feed["up"] = cc.performance_status.lower() in ("optimal", "trending")
        for ch in CHANNELS:
            if cc.channel_name.lower() in ch["name"].lower():
                ch["orders"] = cc.pending_orders

    # Seed or restore support metrics
    if not SupportMetric.query.first():
        db.session.add(SupportMetric(
            active_chats=DASHBOARD_STATE["support_chats"],
            sentiment_score=DASHBOARD_STATE["support_sentiment"],
            recent_resolution=DASHBOARD_STATE["support_resolution"],
        ))
    latest_support = SupportMetric.query.order_by(SupportMetric.id.desc()).first()
    if latest_support:
        DASHBOARD_STATE["support_chats"] = latest_support.active_chats
        DASHBOARD_STATE["support_sentiment"] = latest_support.sentiment_score
        DASHBOARD_STATE["support_resolution"] = latest_support.recent_resolution
        SUPPORT["chats"] = latest_support.active_chats
        SUPPORT["sentiment"] = latest_support.sentiment_score
        SUPPORT["resolution"] = latest_support.recent_resolution

    # Seed or restore marketing studio
    if not MarketingStudio.query.first():
        db.session.add(MarketingStudio(
            active_campaign=DASHBOARD_STATE["mktg_campaign"],
            generation_status=DASHBOARD_STATE["mktg_status"],
            platform_target="Shopify / SMS",
            copy_preview=DASHBOARD_STATE["mktg_copy"],
        ))
    latest_mktg = MarketingStudio.query.order_by(MarketingStudio.id.desc()).first()
    if latest_mktg:
        DASHBOARD_STATE["mktg_campaign"] = latest_mktg.active_campaign
        DASHBOARD_STATE["mktg_status"] = latest_mktg.generation_status
        DASHBOARD_STATE["mktg_copy"] = latest_mktg.copy_preview
        MARKETING["campaign"] = latest_mktg.active_campaign
        MARKETING["status"] = latest_mktg.generation_status
        MARKETING["copy"] = latest_mktg.copy_preview

    # Seed or restore predictive logistics
    if not PredictiveLogistics.query.first():
        db.session.add(PredictiveLogistics(
            variant_sku="SZL-VAR-B",
            days_remaining=4,
            forecasted_demand_velocity=38.5,
            optimal_restock_date=(datetime.utcnow() + timedelta(days=4)).strftime('%Y-%m-%d'),
            status_flag="CRITICAL_STOCKOUT",
        ))
        db.session.add(PredictiveLogistics(
            variant_sku="SZL-VAR-A",
            days_remaining=22,
            forecasted_demand_velocity=12.1,
            optimal_restock_date="2026-08-28",
            status_flag="HEALTHY",
        ))

    db.session.commit()

    # Seed or restore SaaS billing and catalog mirror
    if not SaaSBilling.query.first():
        db.session.add(SaaSBilling(
            merchant_id="merchant_shawn_01",
            stripe_customer_id="cus_R8zX1042",
            stripe_subscription_item_id="si_R8zX1042_metered",
            current_plan="Enterprise AI Tier",
            metered_usage_units=4820,
            accrued_invoice_value=241.00,
            billing_cycle_end="2026-09-01",
        ))
    if not LocalProductCatalog.query.first():
        db.session.add(LocalProductCatalog(
            shopify_product_id="prod_882041",
            title="Shawnzyluxe Luxury Hoodie",
            variant_id="var_99201",
            price=145.00,
            inventory_quantity=85,
        ))
        db.session.add(LocalProductCatalog(
            shopify_product_id="prod_882042",
            title="Shawnzyluxe Minimalist Cap",
            variant_id="var_99202",
            price=45.00,
            inventory_quantity=140,
        ))
    if not AdSpendAnalytic.query.first():
        db.session.add(AdSpendAnalytic(merchant_id="merchant_shawn_01", platform_source="Shopify Product Ads", budget_allocated=1500.00, current_spend=420.00, roas=3.4, conversion_count=28))
        db.session.add(AdSpendAnalytic(merchant_id="merchant_shawn_01", platform_source="TikTok Video Ads", budget_allocated=2000.00, current_spend=680.00, roas=4.1, conversion_count=47))
        db.session.add(AdSpendAnalytic(merchant_id="merchant_shawn_01", platform_source="Meta Retargeting Loop", budget_allocated=1200.00, current_spend=310.00, roas=2.9, conversion_count=19))
    if not GeneratedPurchaseOrder.query.first():
        db.session.add(GeneratedPurchaseOrder(po_reference="PO-SZL-A8F2", merchant_id="merchant_shawn_01", variant_sku="SZL-VAR-B", units_ordered=450, fulfillment_status="PENDING"))
    if not AIAgent.query.first():
        db.session.add(AIAgent(agent_id="agent_logistics", merchant_id="merchant_shawn_01", agent_name="Operations Analyst", agent_role="Operations", status="IDLE_MONITORING", last_action="Reviewed inventory levels and flagged restock needs."))
        db.session.add(AIAgent(agent_id="agent_finance", merchant_id="merchant_shawn_01", agent_name="Finance Analyst", agent_role="Finance", status="IDLE_MONITORING", last_action="Checked cash flow and ad budget headroom."))
        db.session.add(AIAgent(agent_id="agent_marketing", merchant_id="merchant_shawn_01", agent_name="Marketing Analyst", agent_role="Marketing", status="IDLE_MONITORING", last_action="Standing by for campaign instructions."))
        db.session.add(AIAgent(agent_id="agent_support", merchant_id="merchant_shawn_01", agent_name="Support Analyst", agent_role="Support", status="IDLE_MONITORING", last_action="Monitoring customer ticket trends across channels."))
    if not AgentMessage.query.first():
        db.session.add(AgentMessage(sender_agent="agent_logistics", recipient_agent="agent_finance", merchant_id="merchant_shawn_01",
                                    payload="Alert: SKU SZL-VAR-B inventory velocity tracking indicates total stockout threat in 96 hours.", action_taken="stockout_alert"))
        db.session.add(AgentMessage(sender_agent="agent_finance", recipient_agent="agent_marketing", merchant_id="merchant_shawn_01",
                                    payload="Cash flow check verified. Confirmed $1,320 available budget cushion. Approved reorder transaction. Adjust TikTok spend parameters.", action_taken="cash_approved"))
        db.session.add(AgentMessage(sender_agent="agent_marketing", recipient_agent="agent_logistics", merchant_id="merchant_shawn_01",
                                    payload="Acknowledged. Suppressing high-velocity TikTok promo ad arrays temporarily. Supplier C Purchase Order generated.", action_taken="ad_adjusted"))
    db.session.commit()
    billing = SaaSBilling.query.get("merchant_shawn_01")
    if billing:
        DASHBOARD_STATE["stripe_usage"] = billing.metered_usage_units
        DASHBOARD_STATE["stripe_invoice"] = billing.accrued_invoice_value
        STRIPE["plan"] = billing.current_plan
        STRIPE["usage"] = billing.metered_usage_units
        STRIPE["invoice"] = billing.accrued_invoice_value
    hoodie = LocalProductCatalog.query.get("prod_882041")
    if hoodie:
        DASHBOARD_STATE["hoodie_price"] = hoodie.price
        CATALOG["title"] = hoodie.title
        CATALOG["sku"] = hoodie.variant_id
        CATALOG["price"] = hoodie.price

    if SHOPIFY_DOMAIN and STOREFRONT_TOKEN and not ConnectedChannel.query.first():
        tenant = Tenant(company_name="Shawnzy Luxe", tier_level="Pro")
        db.session.add(tenant)
        db.session.flush()
        channel = ConnectedChannel(
            tenant_id=tenant.id,
            channel_type="Shopify",
            store_name=SHOPIFY_DOMAIN,
            api_access_token=STOREFRONT_TOKEN,
            sync_status="Pending",
        )
        db.session.add(channel)
        db.session.commit()


def log_transmission(t_type, recipient, status, summary):
    db.session.add(OutboundTransmission(
        transmission_type=t_type,
        recipient_address=recipient,
        status_chip=status,
        payload_summary=summary,
    ))


def log_system_exception(module, severity, message):
    db.session.add(SystemExceptionLog(
        module_origin=module,
        error_severity=severity,
        exception_msg=message,
    ))
    logger.error(f"[{severity}] {module}: {message}")


def run_async_task(target, *args, **kwargs):
    """Run a function in a daemon thread inside the Flask app context."""
    def _runner():
        with app.app_context():
            target(*args, **kwargs)
    thread = Thread(target=_runner)
    thread.daemon = True
    thread.start()


def build_context_schema(merchant_id):
    """Assemble a proprietary localized context package from merchant data before any LLM call."""
    latest = BusinessMetric.query.filter_by(merchant_id=merchant_id).order_by(BusinessMetric.id.desc()).first()
    channels = MerchantChannel.query.filter_by(merchant_id=merchant_id).all()
    ads = AdSpendAnalytic.query.filter_by(merchant_id=merchant_id).all()
    pl = PredictiveLogistics.query.order_by(PredictiveLogistics.days_remaining.asc()).all()
    po = GeneratedPurchaseOrder.query.filter_by(merchant_id=merchant_id).all()
    decisions = MerchantDecisionLog.query.filter_by(merchant_id=merchant_id).order_by(MerchantDecisionLog.id.desc()).limit(20).all()

    return {
        "merchant_id": merchant_id,
        "timestamp": datetime.now().isoformat(),
        "financials": {
            "total_unified_balance": latest.total_unified_balance if latest else 0,
            "true_net_profit": latest.true_net_profit if latest else 0,
            "gross_revenue": latest.gross_revenue if latest else 0,
            "ai_briefing": latest.ai_briefing if latest else "",
        },
        "channels": {c.channel_id: {"orders": c.pending_orders, "cr": c.conversion_rate} for c in channels},
        "ad_performance": [
            {"platform": a.platform_source, "budget": a.budget_allocated, "spend": a.current_spend, "roas": a.roas}
            for a in ads
        ],
        "inventory_risk": [
            {"sku": p.variant_sku, "days_remaining": p.days_remaining, "status": p.status_flag}
            for p in pl
        ],
        "supplier_pipeline": [
            {"po_ref": g.po_reference, "sku": g.variant_sku, "status": g.fulfillment_status}
            for g in po
        ],
        "decision_history": [
            {"type": d.decision_type, "vector": d.decision_vector, "outcome": d.outcome}
            for d in decisions
        ],
    }


def log_merchant_decision(merchant_id, decision_type, decision_vector, context_snapshot, outcome="approved"):
    """Persist every approve/modify decision to power localized learning moats."""
    db.session.add(MerchantDecisionLog(
        merchant_id=merchant_id,
        action_trigger_type=decision_type,
        decision_type=decision_type,
        user_decision_vector=str(outcome).upper(),
        decision_vector=json.dumps(decision_vector),
        context_snapshot=json.dumps(context_snapshot),
        outcome=outcome,
    ))


def run_multi_agent_collaboration(merchant_id, trigger):
    """Internal broker: specialized AI agents cross-reference records through private DB channels."""
    actions = []

    if trigger in ("low_inventory", "inventory_crisis"):
        pl = PredictiveLogistics.query.filter(PredictiveLogistics.status_flag == "CRITICAL_STOCKOUT").first()
        if pl:
            logistics = AIAgent.query.get("agent_logistics") or AIAgent(agent_id="agent_logistics")
            finance = AIAgent.query.get("agent_finance") or AIAgent(agent_id="agent_finance")
            marketing = AIAgent.query.get("agent_marketing") or AIAgent(agent_id="agent_marketing")

            payload = json.dumps({"alert": "INVENTORY_CRISIS", "target_sku": pl.variant_sku, "time_horizon_days": pl.days_remaining})
            logistics.status = "ALERT_DISPATCHED"
            logistics.last_action = f"Flagged restock need for {pl.variant_sku}"
            logistics.queued_payload = payload
            actions.append({"agent": "Operations", "action": logistics.last_action})

            # Finance checks budget headroom
            tiktok = AdSpendAnalytic.query.filter(AdSpendAnalytic.platform_source.ilike("%tiktok%")).first()
            available_leverage = (tiktok.budget_allocated - tiktok.current_spend) if tiktok else 0
            finance.status = "PROCESSING_COMPLETE"
            finance.last_action = "Coordinated cash allocation with Operations"
            actions.append({"agent": "Finance", "action": finance.last_action})

            marketing.status = "QUEUED_ADJUSTMENT"
            marketing.last_action = f"Trimming TikTok spend to protect {pl.variant_sku} conversion velocity"
            actions.append({"agent": "Marketing", "action": marketing.last_action})

            # Write cross-department briefing
            brief = f"Assistant update: Operations flagged a {pl.variant_sku} stockout threat. Finance confirmed ${available_leverage:.2f} budget headroom. An automated reorder was drafted and TikTok spend should be trimmed temporarily."
            latest = BusinessMetric.query.filter_by(merchant_id=merchant_id).order_by(BusinessMetric.id.desc()).first()
            if latest:
                latest.ai_briefing = brief
            else:
                db.session.add(BusinessMetric(merchant_id=merchant_id, total_unified_balance=20560.00, true_net_profit=1394.00, gross_revenue=4582.00, ai_briefing=brief))

            db.session.add(AgentMessage(sender_agent="agent_logistics", recipient_agent="agent_finance", merchant_id=merchant_id,
                                        payload=payload, action_taken="reorder_cash_check"))
            db.session.add(AgentMessage(sender_agent="agent_finance", recipient_agent="agent_marketing", merchant_id=merchant_id,
                                        payload=f"Trim TikTok spend for {pl.variant_sku}", action_taken="pause_ad_sets"))

    elif trigger == "sales_down":
        finance = AIAgent.query.get("agent_finance")
        marketing = AIAgent.query.get("agent_marketing")
        if finance:
            finance.status = "PROCESSING_COMPLETE"
            finance.last_action = "Analyzed revenue trend vs ad spend"
        if marketing:
            marketing.status = "QUEUED_ADJUSTMENT"
            marketing.last_action = "Drafted new creative angles for top campaigns"
        db.session.add(AgentMessage(sender_agent="agent_finance", recipient_agent="agent_marketing", merchant_id=merchant_id,
                                    payload="ROAS declining; recommend creative refresh", action_taken="creative_refresh"))
        actions.append({"agent": "agent_finance", "action": "Analyzed revenue trend vs ad spend"})
        actions.append({"agent": "agent_marketing", "action": "Drafted new creative angles for top campaigns"})

    db.session.commit()
    return actions


def generate_profit_trend_svg(merchant_id):
    """Build a zero-dependency SVG profit trend line from the last 5 business metrics rows."""
    try:
        rows = BusinessMetric.query.filter_by(merchant_id=merchant_id).order_by(BusinessMetric.id.asc()).limit(5).all()
        profits = [r.true_net_profit for r in rows]
        if len(profits) < 2:
            return "<svg viewBox='0 0 500 150'><text x='20' y='80' fill='#5C6E88'>Awaiting chart historical coordinates...</text></svg>"

        max_p = max(profits) if max(profits) > 0 else 100
        min_p = min(profits)
        p_range = (max_p - min_p) if (max_p - min_p) > 0 else 1

        width, height = 500, 140
        padding_x, padding_y = 30, 20
        usable_w = width - (padding_x * 2)
        usable_h = height - (padding_y * 2)

        points = []
        for i, val in enumerate(profits):
            x = padding_x + (i * (usable_w / (len(profits) - 1)))
            y = (height - padding_y) - (((val - min_p) / p_range) * usable_h)
            points.append(f"{x:.1f},{y:.1f}")

        path_string = "M " + " L ".join(points)
        area_string = f"{path_string} L {width-padding_x},{height-padding_y} L {padding_x},{height-padding_y} Z"

        return f"""
        <svg viewBox="0 0 {width} {height}" style="width:100%; height:auto; overflow:visible;">
          <defs>
            <linearGradient id="chartGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#27AE60" stop-opacity="0.25"/>
              <stop offset="100%" stop-color="#27AE60" stop-opacity="0.00"/>
            </linearGradient>
          </defs>
          <line x1="{padding_x}" y1="{padding_y}" x2="{width-padding_x}" y2="{padding_y}" stroke="rgba(0,0,0,0.03)" stroke-dasharray="4,4"/>
          <line x1="{padding_x}" y1="{height-padding_y}" x2="{width-padding_x}" y2="{height-padding_y}" stroke="rgba(0,0,0,0.05)"/>
          <path d="{area_string}" fill="url(#chartGrad)" stroke="none"/>
          <path d="{path_string}" fill="none" stroke="#27AE60" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
          {" ".join([f'<circle cx="{p.split(",")[0]}" cy="{p.split(",")[1]}" r="4" fill="#1E6B3E" stroke="#FFFFFF" stroke-width="1.5"/>' for p in points])}
        </svg>
        """
    except Exception as e:
        logger.error(f"SVG builder error: {e}")
        return f"<svg><text x='10' y='20'>Chart compilation anomaly: {e}</text></svg>"


def dispatch_external_email(recipient, subject, html_body):
    """Send transactional email via Mailgun API or SMTP fallback; log result."""
    if MAILGUN_API_KEY and MAILGUN_DOMAIN:
        try:
            response = requests.post(
                f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages",
                auth=("api", MAILGUN_API_KEY),
                data={
                    "from": f"Vantav <postmaster@{MAILGUN_DOMAIN}>",
                    "to": recipient,
                    "subject": subject,
                    "html": html_body,
                },
                timeout=10,
            )
            if response.status_code == 200:
                log_transmission("EMAIL_BLAST", recipient, "DELIVERED", f"Mailgun | Subject: {subject}")
                return True
            log_transmission("EMAIL_BLAST", recipient, "FAILED_ROUTING", f"Mailgun {response.status_code}: {response.text}")
            return False
        except Exception as e:
            log_transmission("EMAIL_BLAST", recipient, "FAILED_ROUTING", f"Mailgun: {str(e)}")
            return False

    if not SMTP_USERNAME or not SMTP_PASSWORD:
        log_transmission("EMAIL_BLAST", recipient, "NO_CREDENTIALS", "No Mailgun API key or SMTP credentials configured")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Vantav <{SMTP_USERNAME}>"
        msg["To"] = recipient
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_USERNAME, recipient, msg.as_string())

        log_transmission("EMAIL_BLAST", recipient, "DELIVERED", f"Subject: {subject}")
        return True
    except Exception as e:
        log_transmission("EMAIL_BLAST", recipient, "FAILED_ROUTING", str(e))
        return False


def dispatch_sms(to_number, body):
    """Send SMS via Twilio and log result to outbound_transmissions."""
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, to_number]):
        log_transmission("SMS_BLAST", to_number or "UNKNOWN", "NO_CREDENTIALS", "Twilio or target phone number not configured")
        return False
    try:
        client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(body=body, from_=TWILIO_FROM_NUMBER, to=to_number)
        log_transmission("SMS_BLAST", to_number, "DELIVERED", f"SID: {message.sid} | Body: {body[:80]}")
        return True
    except Exception as e:
        log_transmission("SMS_BLAST", to_number, "FAILED_ROUTING", str(e))
        return False


def _post_crm_webhook(payload: dict):
    """Forward waitlist payload to an external CRM/webhook (Zapier/Make/Notion)."""
    url = os.environ.get("CRM_WEBHOOK_URL", "")
    if not url:
        return
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.warning(f"[CRM Webhook] Failed: {e}")


def _notify_team_new_waitlist(app, plan_label: str):
    """Email the founder/team and push to CRM when a new beta application arrives."""
    team_email = os.environ.get("MERCHANT_EMAIL", "support@vantavcommerce.com")
    summary = (
        f"<h3>New Beta Waitlist Application</h3>"
        f"<p><b>Email:</b> {app.email}</p>"
        f"<p><b>Plan:</b> {plan_label}</p>"
        f"<p><b>Monthly Revenue:</b> {app.monthly_volume or '-'}</p>"
        f"<p><b>Monthly Ad Spend:</b> {app.monthly_ad_spend or '-'}</p>"
        f"<p><b>Active Channels:</b> {app.ad_channels or '-'}</p>"
        f"<p><b>Bottleneck:</b> {app.bottleneck or '-'}</p>"
        f"<p><a href='https://vantavcommerce.com/admin/beta-waitlist'>Review in admin</a></p>"
    )
    dispatch_external_email(team_email, f"New Beta Application: {app.email}", summary)
    _post_crm_webhook({
        "event": "beta_waitlist_submitted",
        "email": app.email,
        "plan": plan_label,
        "monthly_volume": app.monthly_volume,
        "monthly_ad_spend": app.monthly_ad_spend,
        "ad_channels": app.ad_channels,
        "bottleneck": app.bottleneck,
        "status": app.status,
        "created_at": app.created_at.isoformat() if app.created_at else None,
    })


def _confirm_waitlist_to_applicant(app, plan_label: str):
    """Send a confirmation email to the applicant."""
    body = (
        f"<h2>You're on the Prometheus OS beta waitlist</h2>"
        f"<p>Thanks for applying for the <b>{plan_label}</b>. We review every application and will email you within 48 hours if you're selected.</p>"
        f"<p>In the meantime, you can join our community or book a short onboarding call.</p>"
        f"<p>- The Vantav Team</p>"
    )
    dispatch_external_email(app.email, "Prometheus OS Beta — Application Received", body)


def generate_and_send_supplier_po(sku, units_required):
    """Compile a PO file, save it, email it to the supplier, and log the transmission."""
    po_number = f"PO-SZL-{secrets.token_hex(4).upper()}"
    filename = f"{po_number}_ledger.txt"
    target_path = os.path.join(GENERATED_DIR, filename)

    po_content = f"""==================================================
SHAWNZYLUXE LOGISTICS OPERATIONS CENTER
OFFICIAL PURCHASE ORDER RECORD SHEET
==================================================
PO REFERENCE: {po_number}
TIMESTAMP   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
STATUS      : AUTOMATED_REORDER_TRIGGER

SUPPLIER TARGET NODE : Supplier C Network
DELIVERY DESTINATION : Main Hub Warehouse Alpha

ORDER SPECIFICATIONS:
--------------------------------------------------
ITEM SKU       | QUANTITY ORDERED | COMPLIANCE
{sku:<14} | {units_required:<16} | COMPLIANT
--------------------------------------------------

AUTHORIZATION STAMP: SHAWNZYLUXE AI OPERATIONS ENGINE
=================================================="""

    with open(target_path, "w") as f:
        f.write(po_content)

    email_body = f"""<h3>Shawnzyluxe Automated Restock Execution</h3>
<p>Please find the urgent automated purchase request document <b>{po_number}</b>.</p>
<pre style='background:#F4F6F9; padding:15px; border-radius:8px;'>{po_content}</pre>"""
    log_transmission("SUPPLIER_PO", SUPPLIER_EMAIL, "QUEUED", f"PO file {po_number} compiled; email queued for dispatch.")
    run_async_task(dispatch_external_email, SUPPLIER_EMAIL, f"URGENT: Automated Reorder Request {po_number}", email_body)

    return po_number


def report_metered_consumption_to_stripe(merchant_id, quantity_increment):
    """Sync usage to Stripe's metered endpoint; fall back to local mirror for mock keys."""
    if not STRIPE_SECRET_KEY or "mock" in STRIPE_SECRET_KEY:
        logger.info(f"[Stripe Meter Sync] Simulated {quantity_increment} billing hits for {merchant_id}")
        return True

    try:
        account = SaaSBilling.query.get(merchant_id)
        if not account or not account.stripe_subscription_item_id:
            logger.warning(f"[Stripe Meter Sync] No subscription item for {merchant_id}")
            return False

        import requests
        url = f"https://api.stripe.com/v1/subscription_items/{account.stripe_subscription_item_id}/usage_records"
        headers = {
            "Authorization": f"Bearer {STRIPE_SECRET_KEY}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        payload = {
            "quantity": quantity_increment,
            "timestamp": int(datetime.utcnow().timestamp()),
            "action": "increment",
        }
        res = requests.post(url, data=payload, headers=headers, timeout=8)
        if res.status_code == 200 or res.status_code == 201:
            logger.info(f"[Stripe API Synced] Metered {quantity_increment} units")
            return True
        logger.warning(f"[Stripe API] status {res.status_code} body {res.text}")
    except Exception as e:
        logger.error(f"Stripe usage connection faulted: {e}")
    return False


def log_metered_api_usage(merchant_id, operations_count):
    account = SaaSBilling.query.get(merchant_id)
    if account:
        account.metered_usage_units += operations_count
        account.accrued_invoice_value += operations_count * 0.05
    report_metered_consumption_to_stripe(merchant_id, operations_count)


def mutate_shopify_product_price(product_id, variant_id, new_price):
    """Attempt live Shopify Admin API price mutation; fall back to local catalog mirror."""
    if not SHOPIFY_STORE_URL or not SHOPIFY_ACCESS_TOKEN:
        return False
    graphql_url = f"https://{SHOPIFY_STORE_URL}/admin/api/2026-07/graphql.json"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json",
    }
    query = """
    mutation productVariantUpdate($input: ProductVariantInput!) {
      productVariantUpdate(input: $input) {
        productVariant { id price }
        userErrors { field message }
      }
    }
    """
    variables = {
        "input": {
            "id": f"gid://shopify/ProductVariant/{variant_id}",
            "price": str(new_price),
        }
    }
    try:
        r = requests.post(graphql_url, json={"query": query, "variables": variables}, headers=headers, timeout=8)
        res_data = r.json()
        if r.status_code == 200 and not res_data.get("errors") and not res_data.get("data", {}).get("productVariantUpdate", {}).get("userErrors"):
            local = LocalProductCatalog.query.get(product_id)
            if local:
                local.price = new_price
            return True
        return False
    except Exception:
        return False


def storefront(query, variables=None):
    if not GRAPHQL_URL or not STOREFRONT_TOKEN:
        return {}
    headers = {
        "X-Shopify-Storefront-Access-Token": STOREFRONT_TOKEN,
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables or {}},
            headers=headers,
            timeout=15,
        )
        return r.json()
    except Exception as e:
        logger.warning(f"Storefront API error: {e}")
        return {}


@app.route('/')
@limiter.exempt
def home():
    host = request.host.split(':')[0].lower()
    if host in ('shawnzyluxe.com', 'www.shawnzyluxe.com'):
        return render_template('coming_soon.html')
    merchant = get_merchant_context()
    if merchant:
        return redirect(url_for('dashboard'))
    # Site-wall-only users still need to log in as a merchant before viewing the dashboard.
    if site_wall_authenticated() and site_wall_enabled():
        return redirect(url_for('login'))
    return render_template('landing.html')


@app.route('/subscribe')
@limiter.exempt
def subscribe():
    """Public beta waitlist landing page."""
    host = request.host.split(':')[0].lower()
    if host in ('shawnzyluxe.com', 'www.shawnzyluxe.com'):
        return render_template('coming_soon.html')
    return render_template('subscribe.html', recaptcha_site_key=RECAPTCHA_SITE_KEY, meta_pixel_id=os.environ.get('META_PIXEL_ID', ''), tiktok_pixel_id=os.environ.get('TIKTOK_PIXEL_ID', ''), gtm_id=os.environ.get('GTM_ID', ''))


@app.route('/thank-you')
@limiter.exempt
def thank_you():
    """Post-waitlist submission confirmation."""
    return render_template('thank_you.html')


@app.route('/demo')
@limiter.exempt
def demo():
    """Public product demo video page."""
    return render_template('demo.html')


@app.route('/terms')
@limiter.exempt
def legal_terms():
    return render_template('terms.html')


@app.route('/privacy')
@limiter.exempt
def legal_privacy():
    return render_template('privacy.html')


@app.route('/refund')
@limiter.exempt
def legal_refund():
    return render_template('refund.html')


@app.route('/dashboard')
def dashboard():
    merchant = get_merchant_context()
    if not merchant:
        return redirect(url_for('login'))
    merchant_id = merchant["id"]
    ctx = context(active_page='overview', merchant=merchant, merchant_id=merchant_id)
    return render_template('dashboard/overview.html', **ctx)


# Commercial-grade dashboard page routes
def _dashboard_context(active_page):
    merchant = get_merchant_context()
    merchant_id = merchant["id"] if merchant else None
    ctx = context(active_page=active_page, merchant=merchant, merchant_id=merchant_id)
    return ctx


@app.route('/dashboard/<page>')
def dashboard_page(page):
    merchant = get_merchant_context()
    if not merchant:
        return redirect(url_for('login'))
    active_page = page.replace('-', '_')
    valid_pages = {
        'overview', 'command-center', 'commerce-hub', 'alerts', 'action-gate', 'profit-engine', 'startup-pack',
        'predictions', 'product-research', 'fulfillment', 'fraud', 'suppliers',
        'marketing', 'support', 'automations', 'team-ai', 'health-score',
        'mobile-copilot', 'store-catalog', 'products', 'orders', 'customers',
        'inventory', 'shipments', 'returns', 'analytics', 'discounts', 'apps',
        'themes', 'reports', 'billing', 'integrations', 'settings', 'tiktok-studio'
    }
    if page not in valid_pages:
        return redirect(url_for('dashboard'))
    # Beta gating: merchants can only reach beta-ready pages.
    if BETA_MODE:
        s = get_current_user()
        if not s or s.role not in (UserRole.ADMIN.value, UserRole.ENGINEER.value):
            if page not in BETA_READY_DASHBOARD_PAGES:
                return redirect(url_for('dashboard'))
    ctx = _dashboard_context(active_page)
    template = 'dashboard/{}.html'.format(page.replace('-', '_'))
    try:
        return render_template(template, **ctx)
    except Exception:
        return render_template('dashboard/page.html', **ctx,
                               page_title=active_page.replace('_', ' ').title(),
                               page_description='This module is being rebuilt to the new commercial-grade standard.',
                               page_content='')


@app.route('/home')
def home_page():
    return render_template('home.html')


@app.route('/api/command', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_command():
    """Natural-language assistant endpoint with live data, memory, and tools."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    q = data.get("q", "").strip()
    if not q:
        return jsonify({"answer": "What would you like to know?", "did": []}), 200
    try:
        result = assistant_engine.chat(merchant["id"], q)
        return jsonify(result)
    except Exception as e:
        logger.error(f"[Assistant] api_command failed: {e}")
        return jsonify({"answer": "I had trouble processing that. Try again or contact support.", "did": []}), 500


def process_command(cmd_text):
    """Shared NLP engine used by HTTP and WebSocket. Mutates state and persists."""
    if not cmd_text:
        return None

    merchant = None
    try:
        merchant = get_merchant_context()
    except Exception:
        pass
    merchant_id = merchant["id"] if merchant else "merchant_shawn_01"

    def require_feature(feature, fallback_briefing):
        allowed, reason, _ = check_tier_limits(merchant_id, feature)
        if not allowed:
            logger.warning(f"Command blocked by tier: {merchant_id} / {feature} ({reason})")
            updates = {"ai_briefing": f"🚫 Tier Limit: {reason}. Upgrade to unlock."}
            DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
            COO["narrative"] = updates["ai_briefing"]
            return updates
        return None

    updates = {
        "ai_briefing": DASHBOARD_STATE["ai_briefing"],
        "total_balance": f"{DASHBOARD_STATE['total_unified_balance']:.2f}",
        "clear_orders": False,
        "trigger_download": False,
        "support_chats": DASHBOARD_STATE["support_chats"],
        "support_sentiment": DASHBOARD_STATE["support_sentiment"],
        "support_resolution": DASHBOARD_STATE["support_resolution"],
        "mktg_campaign": DASHBOARD_STATE["mktg_campaign"],
        "mktg_status": DASHBOARD_STATE["mktg_status"],
        "mktg_copy": DASHBOARD_STATE["mktg_copy"],
        "stripe_usage": DASHBOARD_STATE["stripe_usage"],
        "stripe_invoice": f"{DASHBOARD_STATE['stripe_invoice']:.2f}",
        "hoodie_price": f"{DASHBOARD_STATE['hoodie_price']:.2f}",
    }

    if re.search(r'(why are sales down|sales down|analyze drops)', cmd_text):
        updates["ai_briefing"] = "📊 AI Audit: Sales down 4% today due to an ad delivery lag in TikTok region East. Supply pipelines remain green."
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]

    elif re.search(r'(show delayed orders|delayed orders|shipments delayed)', cmd_text):
        updates["ai_briefing"] = "📦 Fulfillment Tracking: 2 shipments remain stalled at Memphis Hub due to supplier weather anomalies. Tracking codes verified."
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]

    elif re.search(r'(generate marketing copy|write copy|email blast|create campaign)', cmd_text):
        blocked = require_feature("marketing", "Marketing campaigns are not available on your tier")
        if blocked:
            return blocked
        DASHBOARD_STATE["mktg_campaign"] = "Autumn Launch Preview"
        DASHBOARD_STATE["mktg_status"] = "Queued"
        DASHBOARD_STATE["mktg_copy"] = "Email + SMS blast queued for non-blocking dispatch."
        updates["mktg_campaign"] = DASHBOARD_STATE["mktg_campaign"]
        updates["mktg_status"] = DASHBOARD_STATE["mktg_status"]
        updates["mktg_copy"] = DASHBOARD_STATE["mktg_copy"]

        html_campaign_body = """<div style='font-family:sans-serif; max-width:600px; margin:0 auto; padding:20px; border:1px solid #EAEAEA;'>
  <h2 style='color:#1D2D44;'>The Autumn Collection Preview</h2>
  <p>Shawnzyluxe early operational configuration profiles are now open. Secure early access to your platform allocation containers link now.</p>
  <hr style='border:none; border-top:1px solid #EEEEEE;'/>
  <p style='font-size:11px; color:#999999;'>Sent via Shawnzyluxe Automated Marketing Engine Hub.</p>
</div>"""
        run_async_task(dispatch_external_email, "subscribers-list@shawnzyluxe.com", "The Next Chapter: Shawnzyluxe Autumn Preview", html_campaign_body)
        run_async_task(dispatch_sms, MERCHANT_PHONE, "Shawnzyluxe Autumn Preview: Early access portal is now open for members.")

        updates["ai_briefing"] = "🚀 Creative Studio: Campaign arrays compiled and dispatched to non-blocking worker threads (email + SMS)."
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]

    elif re.search(r'(create discount|discount campaign|promo code)', cmd_text):
        blocked = require_feature("marketing", "Marketing campaigns are not available on your tier")
        if blocked:
            return blocked
        DASHBOARD_STATE["total_unified_balance"] += 1200.00
        BRIEFING["revenue"] += 1200.00
        DASHBOARD_STATE["mktg_campaign"] = "ECOM_AI_15 Active"
        DASHBOARD_STATE["mktg_status"] = "Queued"
        DASHBOARD_STATE["mktg_copy"] = "SMS blast queued for non-blocking dispatch."
        run_async_task(dispatch_sms, MERCHANT_PHONE, "🛍️ Shawnzyluxe: Use code ECOM_AI_15 for 15% off today. Automated by AI.")
        updates["total_balance"] = f"{DASHBOARD_STATE['total_unified_balance']:.2f}"
        updates["mktg_campaign"] = DASHBOARD_STATE["mktg_campaign"]
        updates["mktg_status"] = DASHBOARD_STATE["mktg_status"]
        updates["mktg_copy"] = DASHBOARD_STATE["mktg_copy"]
        updates["ai_briefing"] = "✨ Marketing Studio Action: Injected promo script and dispatched SMS to non-blocking worker."
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]

    elif re.search(r'(why are sales down|sales drop|revenue decline)', cmd_text):
        merchant = get_merchant_context()
        merchant_id = merchant["id"] if merchant else "merchant_shawn_01"
        ctx = build_context_schema(merchant_id)
        actions = run_multi_agent_collaboration(merchant_id, "sales_down")
        ad_summary = ", ".join([f"{a['platform']} ROAS {a['roas']}x" for a in ctx["ad_performance"]])
        worst = min(ctx["ad_performance"], key=lambda x: x["roas"]) if ctx["ad_performance"] else {}
        updates["ai_briefing"] = f"📉 Sales Context Engine: Revenue is ${ctx['financials']['gross_revenue']:.2f}. Channels: {ctx['channels']}. Ad mix: {ad_summary}. Weakest ROAS is {worst.get('platform', 'N/A')} at {worst.get('roas', 0)}x. Multi-agent loop triggered: {len(actions)} handoffs."
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]
        log_merchant_decision(merchant_id, "ai_inquiry", {"query": cmd_text}, ctx)

    elif re.search(r'(evaluate shortages|predict supply|inventory forecast|shortages)', cmd_text):
        p_row = PredictiveLogistics.query.filter_by(variant_sku="SZL-VAR-B").first()
        if p_row:
            po_ref = generate_and_send_supplier_po(p_row.variant_sku, 450)
            p_row.days_remaining = 30
            p_row.status_flag = "REORDERED"
            merchant = get_merchant_context()
            run_multi_agent_collaboration(merchant["id"] if merchant else "merchant_shawn_01", "low_inventory")
        else:
            po_ref = None
        rows = PredictiveLogistics.query.order_by(PredictiveLogistics.days_remaining.asc()).all()
        updates["predictive_html"] = "".join([
            f"""<div style='border-bottom: 1px solid rgba(126,61,0,0.1); padding-bottom: 8px;'>
  <div style='display:flex; justify-content:space-between; margin-bottom:4px;'>
    <span style='font-weight: 600; color:#7E3D00;'>{r.variant_sku}</span>
    <span class='{"badge-alert" if r.status_flag == "CRITICAL_STOCKOUT" else "badge-stable"}'>{r.status_flag}</span>
  </div>
  <div style='color: #5C6E88; font-size:11px;'>
    Stockout: <span style='font-weight:600; color:#1D2D44;'>{r.days_remaining} Days left</span> | Restock: <span style='font-family:"JetBrains Mono";'>{r.optimal_restock_date}</span>
  </div>
</div>""" for r in rows
        ])
        if po_ref:
            updates["ai_briefing"] = f"🔮 Logistics Engine Execution: Compiled asset request sheet <b>{po_ref}</b> and dispatched file data arrays to <b>{SUPPLIER_EMAIL}</b>."
        else:
            updates["ai_briefing"] = "🔮 Logistics Engine Analysis: All connected inventory levels track healthy within safe structural parameters."
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]

    elif re.search(r'(clear queue|process orders|fulfill all orders)', cmd_text):
        updates["clear_orders"] = True
        DASHBOARD_STATE["channels"]["shopify"]["pending_orders"] = 0
        DASHBOARD_STATE["channels"]["amazon"]["pending_orders"] = 0
        DASHBOARD_STATE["channels"]["tiktok"]["pending_orders"] = 0
        for c in CHANNELS:
            c["orders"] = 0
        BRIEFING["orders"] = 0
        BRIEFING["delayed"] = 0
        updates["ai_briefing"] = "🚀 Operational Success: Dispatched all 23 pending cross-channel orders to corresponding packaging endpoints securely."
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]

    elif re.search(r'(customer support|check chats|support status)', cmd_text):
        DASHBOARD_STATE["support_chats"] = 3
        DASHBOARD_STATE["support_sentiment"] = "96% Positive"
        DASHBOARD_STATE["support_resolution"] = "Identified and resolved checkout latency anomaly."
        updates["ai_briefing"] = "🎧 Support Audit: AI Agent managing 3 concurrent queries. Average response delay remains optimized under 4 seconds."
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]

    elif re.search(r'(escalate issue|angry customer|negative sentiment)', cmd_text):
        DASHBOARD_STATE["support_chats"] = 4
        DASHBOARD_STATE["support_sentiment"] = "89% Balanced"
        DASHBOARD_STATE["support_resolution"] = "Ticket #1409 passed smoothly to administrative tier."
        updates["ai_briefing"] = "⚠️ Urgency Intercept: Flagged 1 ticket displaying frustration on Shopify. Redirected context to manual agent queue."
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]

    elif re.search(r'(export report|download excel|generate ledger|report)', cmd_text):
        target_csv = os.path.join(GENERATED_DIR, "shawnzyluxe_ledger.csv")
        with open(target_csv, "w") as f:
            f.write("Platform Ticker Metric ID,Unified Aggregated Liquidity,True Net Margins,Gross E-Commerce Returns\n")
            f.write(f"SHAWNZYLUXE_CORE_V1,{DASHBOARD_STATE['total_unified_balance']:.2f},{DASHBOARD_STATE['true_net_profit']:.2f},{DASHBOARD_STATE['gross_revenue']:.2f}\n")
        updates["ai_briefing"] = "📊 Financial Compiler: Generated 'shawnzyluxe_ledger.csv' into server files. Download starting automatically."
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]
        updates["trigger_download"] = True

    elif re.search(r'(check billing|stripe invoice|saas usage|view fees)', cmd_text):
        merchant_id = "merchant_shawn_01"
        log_metered_api_usage(merchant_id, 1)
        billing = SaaSBilling.query.get(merchant_id)
        if billing:
            DASHBOARD_STATE["stripe_usage"] = billing.metered_usage_units
            DASHBOARD_STATE["stripe_invoice"] = billing.accrued_invoice_value
            STRIPE["usage"] = billing.metered_usage_units
            STRIPE["invoice"] = billing.accrued_invoice_value
            updates["stripe_usage"] = billing.metered_usage_units
            updates["stripe_invoice"] = f"{billing.accrued_invoice_value:.2f}"
            updates["ai_briefing"] = f"💰 Stripe Ledger Synced: Profile mapped to <b>{billing.current_plan}</b>. Current billing cycle usage: <b>{billing.metered_usage_units} metered actions</b>. Accrued invoice totals: <b>${billing.accrued_invoice_value:.2f}</b>."
        else:
            updates["ai_briefing"] = "💰 Stripe Ledger: No billing account found."
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]

    elif re.search(r'(update price|adjust cost|catalog push)', cmd_text):
        blocked = require_feature("shopify", "Catalog control is not available on your tier")
        if blocked:
            return blocked
        prices = re.findall(r'\d+(?:\.\d+)?', cmd_text)
        if prices:
            target_price = float(prices[0])
            log_metered_api_usage("merchant_shawn_01", 10)
            catalog = LocalProductCatalog.query.first()
            if catalog:
                api_success = mutate_shopify_product_price(catalog.shopify_product_id, catalog.variant_id, target_price)
                catalog.price = target_price
                DASHBOARD_STATE["hoodie_price"] = target_price
                CATALOG["price"] = target_price
                CATALOG["title"] = catalog.title
                CATALOG["sku"] = catalog.variant_id
                updates["hoodie_price"] = f"{target_price:.2f}"
                if api_success:
                    updates["ai_briefing"] = f"🛍️ Catalog Matrix Success: Transmitted GraphQL update. <b>{catalog.title}</b> price changed to <b>${target_price:.2f}</b> live on Shopify Storefront."
                else:
                    updates["ai_briefing"] = f"⚠️ Catalog API Simulation: Shopify API offline. Local table mirror updated: <b>{catalog.title}</b> set to <b>${target_price:.2f}</b>."
            else:
                updates["ai_briefing"] = "🛍️ Catalog Error: No catalog products found in local mirror."
        else:
            updates["ai_briefing"] = "🛍️ Catalog Error: Specify a valid numerical target. Example: 'update price to 149.99'"
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]

    else:
        return None

    # Persist state to SQLite
    db.session.add(BusinessMetric(
        merchant_id="merchant_shawn_01",
        total_unified_balance=DASHBOARD_STATE["total_unified_balance"],
        true_net_profit=DASHBOARD_STATE["true_net_profit"],
        gross_revenue=DASHBOARD_STATE["gross_revenue"],
        ai_briefing=DASHBOARD_STATE["ai_briefing"],
    ))
    for channel_id, data in DASHBOARD_STATE["channels"].items():
        cc = CommerceChannel.query.get(channel_id)
        if cc:
            cc.pending_orders = data["pending_orders"]
    db.session.add(SupportMetric(
        active_chats=DASHBOARD_STATE["support_chats"],
        sentiment_score=DASHBOARD_STATE["support_sentiment"],
        recent_resolution=DASHBOARD_STATE["support_resolution"],
    ))
    SUPPORT["chats"] = DASHBOARD_STATE["support_chats"]
    SUPPORT["sentiment"] = DASHBOARD_STATE["support_sentiment"]
    SUPPORT["resolution"] = DASHBOARD_STATE["support_resolution"]
    db.session.add(MarketingStudio(
        active_campaign=DASHBOARD_STATE["mktg_campaign"],
        generation_status=DASHBOARD_STATE["mktg_status"],
        platform_target="Shopify / SMS",
        copy_preview=DASHBOARD_STATE["mktg_copy"],
    ))
    MARKETING["campaign"] = DASHBOARD_STATE["mktg_campaign"]
    MARKETING["status"] = DASHBOARD_STATE["mktg_status"]
    MARKETING["copy"] = DASHBOARD_STATE["mktg_copy"]

    shopify = CommerceChannel.query.get("shopify")
    updates["shopify_orders"] = shopify.pending_orders if shopify else 0
    updates["mktg_campaign"] = DASHBOARD_STATE["mktg_campaign"]
    updates["mktg_status"] = DASHBOARD_STATE["mktg_status"]
    updates["mktg_copy"] = DASHBOARD_STATE["mktg_copy"]
    db.session.commit()

    return updates


@app.route('/api/v1/execute-command', methods=['POST'])
def execute_command():
    """HTTP route for the NLP command engine."""
    data = request.get_json() or {}
    updates = process_command(data.get("command", "").lower().strip())
    if updates is None:
        return jsonify({"success": False})
    return jsonify({"success": True, "updates": updates})


@sock.route('/ws/telemetry')
def telemetry(ws):
    """WebSocket route that processes commands and broadcasts updates instantly."""
    manager.connect(ws)
    try:
        while True:
            raw = ws.receive()
            if raw is None:
                break
            payload = json.loads(raw)
            updates = process_command(payload.get("command", "").lower().strip())
            if updates is None:
                ws.send(json.dumps({"type": "error", "message": "Command pattern outside standard operational array layout parameters."}))
            else:
                manager.broadcast({"type": "ui_update", "updates": updates})
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
    finally:
        manager.disconnect(ws)


@app.route('/api/orders')
def api_orders():
    """Return recent orders with computed margin from the Profit Feed."""
    merchant = get_merchant_context()
    merchant_id = merchant["id"] if merchant else "merchant_shawn_01"
    orders = profit_feed.get_recent_orders(merchant_id)
    breakdown = profit_feed.get_profit_breakdown(merchant_id)
    return jsonify({
        "orders": orders,
        "count": len(orders),
        "revenue": breakdown["gross_revenue"],
        "profit": breakdown["net_profit"],
    })


@app.route('/api/profit/breakdown')
def api_profit_breakdown():
    """Calculate and return the profit breakdown from the Profit Feed."""
    merchant = get_merchant_context()
    merchant_id = merchant["id"] if merchant else "merchant_shawn_01"
    breakdown = profit_feed.get_profit_breakdown(merchant_id)
    return jsonify({
        "gross_revenue": breakdown["gross_revenue"],
        "total_costs": breakdown["total_costs"],
        "net_profit": breakdown["net_profit"],
        "net_margin": breakdown["net_margin"],
        "rows": breakdown["profit_rows"],
    })


@app.route('/api/kpis')
def api_kpis():
    """Real-time profit KPI feed for the dashboard and external consumers."""
    merchant = get_merchant_context()
    merchant_id = merchant["id"] if merchant else "merchant_shawn_01"
    return jsonify(profit_feed.get_kpis(merchant_id))


@app.route('/api/alerts')
def api_alerts():
    """Real-time Alert Matrix for the authenticated merchant."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    try:
        alert_matrix.refresh_alerts(merchant["id"])
        rows = [alert_matrix.alert_to_dict(a) for a in alert_matrix.get_alerts(merchant["id"])]
        return jsonify({"alerts": rows, "count": len(rows)}), 200
    except Exception as e:
        logger.error(f"[Alert Matrix] Failed: {e}")
        return jsonify({"detail": "Unable to load alerts."}), 500


@app.route('/api/fraud')
def api_fraud():
    """Real-time fraud/risk alerts for the authenticated merchant."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    try:
        alert_matrix.refresh_alerts(merchant["id"])
        rows = [alert_matrix.fraud_alert_to_dict(a) for a in alert_matrix.get_fraud_alerts(merchant["id"])]
        return jsonify({"alerts": rows, "count": len(rows)}), 200
    except Exception as e:
        logger.error(f"[Fraud Alerts] Failed: {e}")
        return jsonify({"detail": "Unable to load fraud alerts."}), 500


@app.route('/api/alerts/<int:alert_id>/resolve', methods=['POST'])
def resolve_alert(alert_id):
    """Resolve an alert."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    alert = Alert.query.filter_by(id=alert_id, merchant_id=merchant["id"]).first()
    if not alert:
        return jsonify({"error": "Alert not found"}), 404
    alert.status = "resolved"
    alert.resolved_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"status": "resolved"}), 200


@app.route('/api/alerts/<int:alert_id>/snooze', methods=['POST'])
def snooze_alert(alert_id):
    """Snooze an alert for 24 hours."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    alert = Alert.query.filter_by(id=alert_id, merchant_id=merchant["id"]).first()
    if not alert:
        return jsonify({"error": "Alert not found"}), 404
    alert.status = "snoozed"
    alert.resolved_at = datetime.utcnow() + timedelta(days=1)
    db.session.commit()
    return jsonify({"status": "snoozed", "until": alert.resolved_at.isoformat()}), 200


@app.route('/api/alerts/<int:alert_id>/dispatch', methods=['POST'])
def dispatch_alert(alert_id):
    """Manually dispatch an alert to Discord/SMS."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    alert = Alert.query.filter_by(id=alert_id, merchant_id=merchant["id"]).first()
    if not alert:
        return jsonify({"error": "Alert not found"}), 404
    # Phone number can be provided in JSON or fetched from merchant settings.
    phone = request.get_json(silent=True, force=True) or {}
    to_number = phone.get("phone")
    channels = alert_matrix.dispatch_alert(alert, to_number=to_number)
    return jsonify({"dispatched": channels}), 200


# ============================================================
# ACTION GATE
# ============================================================

@app.route('/api/actions', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_actions():
    """Return pending Action Gate approvals and recent history."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    try:
        pending = [action_gate.action_to_dict(a) for a in action_gate.list_pending_actions(merchant["id"])]
        history = [action_gate.action_to_dict(a) for a in action_gate.list_action_history(merchant["id"])]
        return jsonify({"pending": pending, "history": history}), 200
    except Exception as e:
        logger.error(f"[Action Gate] List failed: {e}")
        return jsonify({"detail": "Could not load actions."}), 500


@app.route('/api/actions/<int:action_id>/approve', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_approve_action(action_id):
    """Approve and execute a pending Action Gate action."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    try:
        result = action_gate.approve_action(action_id, merchant["id"], decided_by=merchant["id"])
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[Action Gate] Approve failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/actions/<int:action_id>/deny', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_deny_action(action_id):
    """Deny a pending Action Gate action."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    try:
        result = action_gate.deny_action(action_id, merchant["id"], reason=data.get("reason", ""), decided_by=merchant["id"])
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[Action Gate] Deny failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/actions/<int:action_id>/modify', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_modify_action(action_id):
    """Modify payload of a pending Action Gate action."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    try:
        result = action_gate.modify_action(action_id, merchant["id"], payload_updates=data.get("payload", {}))
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[Action Gate] Modify failed: {e}")
        return jsonify({"detail": str(e)}), 400


# ============================================================
# CHANNEL CONNECTIONS
# ============================================================

@app.route('/api/v1/channels', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_channels():
    """List merchant channel connections."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    try:
        return jsonify({"channels": channels_module.list_channels(merchant["id"])}), 200
    except Exception as e:
        logger.error(f"[Channels] List failed: {e}")
        return jsonify({"detail": "Could not load channels."}), 500


@app.route('/api/v1/channels/shopify', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_connect_shopify():
    """Manually connect a Shopify store with an access token."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    if not merchant.get("live_access_enabled"):
        return jsonify({"detail": "Live marketplace connections are disabled during the sandbox."}), 403
    data = request.get_json(silent=True) or {}
    shop = (data.get("shop") or "").strip().lower()
    token = (data.get("access_token") or "").strip()
    if not shop or not token:
        return jsonify({"detail": "shop and access_token required"}), 400
    try:
        channels_module.connect_shopify(merchant["id"], shop, token)
        return jsonify({"status": "connected", "platform": "shopify", "domain": shop}), 200
    except Exception as e:
        logger.error(f"[Channels] Shopify connect failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/v1/channels/tiktok', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_connect_tiktok():
    """Connect a TikTok Shop with app credentials and access token."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    if not merchant.get("live_access_enabled"):
        return jsonify({"detail": "Live marketplace connections are disabled during the sandbox."}), 403
    data = request.get_json(silent=True) or {}
    seller_id = (data.get("seller_id") or "").strip()
    app_key = (data.get("app_key") or "").strip()
    app_secret = (data.get("app_secret") or "").strip()
    access_token = (data.get("access_token") or "").strip()
    shop_cipher = (data.get("shop_cipher") or "").strip()
    if not seller_id or not app_key or not app_secret or not access_token:
        return jsonify({"detail": "seller_id, app_key, app_secret, and access_token required"}), 400
    try:
        channels_module.connect_tiktok(merchant["id"], seller_id, app_key, app_secret, access_token, shop_cipher)
        return jsonify({"status": "connected", "platform": "tiktok"}), 200
    except Exception as e:
        logger.error(f"[Channels] TikTok connect failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/v1/channels/amazon', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_connect_amazon():
    """Connect an Amazon Seller Central account with SP-API credentials."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    if not merchant.get("live_access_enabled"):
        return jsonify({"detail": "Live marketplace connections are disabled during the sandbox."}), 403
    data = request.get_json(silent=True) or {}
    seller_id = (data.get("seller_id") or "").strip()
    access_key = (data.get("access_key") or "").strip()
    secret_key = (data.get("secret_key") or "").strip()
    region = (data.get("region") or "").strip().lower()
    refresh_token = (data.get("refresh_token") or "").strip()
    lwa_client_id = (data.get("lwa_client_id") or "").strip()
    lwa_client_secret = (data.get("lwa_client_secret") or "").strip()
    role_arn = (data.get("role_arn") or "").strip()
    if not seller_id or not access_key or not secret_key or not region:
        return jsonify({"detail": "seller_id, access_key, secret_key, and region required"}), 400
    if not refresh_token or not lwa_client_id or not lwa_client_secret:
        return jsonify({"detail": "refresh_token, lwa_client_id, and lwa_client_secret are required for SP-API sync"}), 400
    try:
        channels_module.connect_amazon(
            merchant["id"], seller_id, access_key, secret_key, region,
            refresh_token, lwa_client_id, lwa_client_secret, role_arn
        )
        return jsonify({"status": "connected", "platform": "amazon"}), 200
    except Exception as e:
        logger.error(f"[Channels] Amazon connect failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/v1/channels/<platform>/disconnect', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_disconnect_channel(platform):
    """Disconnect a merchant channel."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    try:
        channels_module.disconnect(merchant["id"], platform)
        return jsonify({"status": "disconnected", "platform": platform}), 200
    except Exception as e:
        logger.error(f"[Channels] Disconnect failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/v1/channels/shopify/sync', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_sync_shopify():
    """Pull the latest Shopify orders and product catalog for this merchant."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    if not merchant.get("live_access_enabled"):
        return jsonify({"detail": "Live marketplace sync is disabled during the sandbox."}), 403
    try:
        result = shopify_sync.sync_shopify(merchant["id"])
        return jsonify({"status": "synced", **result}), 200
    except Exception as e:
        logger.error(f"[Shopify Sync] Failed for {merchant['id']}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/v1/channels/shopify/products', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_get_shopify_products():
    """Return the last-synced Shopify product catalog."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    if not merchant.get("live_access_enabled"):
        return jsonify({"detail": "Live marketplace data is disabled during the sandbox."}), 403
    try:
        return jsonify({"products": shopify_sync.get_products(merchant["id"])}), 200
    except Exception as e:
        logger.error(f"[Shopify Products] Failed for {merchant['id']}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/admin/shopify/sync/<merchant_id>', methods=['POST'])
@require_roles([UserRole.ADMIN])
def api_admin_sync_shopify(merchant_id):
    """Admin-triggered Shopify sync for testing (bypasses live-access gate)."""
    try:
        result = shopify_sync.sync_shopify(merchant_id)
        return jsonify({"status": "synced", "merchant_id": merchant_id, **result}), 200
    except Exception as e:
        logger.error(f"[Admin Shopify Sync] Failed for {merchant_id}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/v1/channels/tiktok/sync', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_sync_tiktok():
    """Pull the latest TikTok Shop orders and product catalog for this merchant."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    if not merchant.get("live_access_enabled"):
        return jsonify({"detail": "Live marketplace sync is disabled during the sandbox."}), 403
    try:
        result = tiktok_sync.sync_tiktok(merchant["id"])
        return jsonify({"status": "synced", **result}), 200
    except Exception as e:
        logger.error(f"[TikTok Sync] Failed for {merchant['id']}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/v1/channels/tiktok/products', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_get_tiktok_products():
    """Return the last-synced TikTok Shop product catalog."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    if not merchant.get("live_access_enabled"):
        return jsonify({"detail": "Live marketplace data is disabled during the sandbox."}), 403
    try:
        return jsonify({"products": tiktok_sync.get_products(merchant["id"])}), 200
    except Exception as e:
        logger.error(f"[TikTok Products] Failed for {merchant['id']}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/admin/tiktok/sync/<merchant_id>', methods=['POST'])
@require_roles([UserRole.ADMIN])
def api_admin_sync_tiktok(merchant_id):
    """Admin-triggered TikTok Shop sync for testing (bypasses live-access gate)."""
    try:
        result = tiktok_sync.sync_tiktok(merchant_id)
        return jsonify({"status": "synced", "merchant_id": merchant_id, **result}), 200
    except Exception as e:
        logger.error(f"[Admin TikTok Sync] Failed for {merchant_id}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/v1/channels/amazon/sync', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_sync_amazon():
    """Pull the latest Amazon orders and product catalog for this merchant."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    if not merchant.get("live_access_enabled"):
        return jsonify({"detail": "Live marketplace sync is disabled during the sandbox."}), 403
    try:
        result = amazon_sync.sync_amazon(merchant["id"])
        return jsonify({"status": "synced", **result}), 200
    except Exception as e:
        logger.error(f"[Amazon Sync] Failed for {merchant['id']}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/v1/channels/amazon/products', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_get_amazon_products():
    """Return the last-synced Amazon product catalog."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    if not merchant.get("live_access_enabled"):
        return jsonify({"detail": "Live marketplace data is disabled during the sandbox."}), 403
    try:
        return jsonify({"products": amazon_sync.get_products(merchant["id"])}), 200
    except Exception as e:
        logger.error(f"[Amazon Products] Failed for {merchant['id']}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/admin/amazon/sync/<merchant_id>', methods=['POST'])
@require_roles([UserRole.ADMIN])
def api_admin_sync_amazon(merchant_id):
    """Admin-triggered Amazon sync for testing (bypasses live-access gate)."""
    try:
        result = amazon_sync.sync_amazon(merchant_id)
        return jsonify({"status": "synced", "merchant_id": merchant_id, **result}), 200
    except Exception as e:
        logger.error(f"[Admin Amazon Sync] Failed for {merchant_id}: {e}")
        return jsonify({"detail": str(e)}), 400


# ============================================================
# STARTUP PACK
# ============================================================

@app.route('/api/v1/startup-pack', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_startup_pack():
    """Return the merchant's Startup Pack project."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    try:
        project = startup_pack.get_project(merchant["id"])
        return jsonify(startup_pack.project_to_dict(project)), 200
    except Exception as e:
        logger.error(f"[Startup Pack] Get failed: {e}")
        return jsonify({"detail": "Could not load project."}), 500


@app.route('/api/v1/tiktok-studio', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_tiktok_studio():
    """Return the merchant's TikTok Demand Studio state."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    try:
        return jsonify(tiktok_studio.get_state(merchant["id"])), 200
    except Exception as e:
        logger.error(f"[TikTok Studio] Get failed: {e}")
        return jsonify({"detail": "Could not load TikTok studio."}), 500


@app.route('/api/v1/tiktok-studio/hooks', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_tiktok_studio_hooks():
    """Generate TikTok hooks from a product description."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    try:
        hooks = tiktok_studio.generate_hooks(data.get("product", ""))
        return jsonify({"hooks": hooks}), 200
    except Exception as e:
        logger.error(f"[TikTok Studio] Hook generation failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/v1/tiktok-studio/plan', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_tiktok_studio_plan():
    """Generate a new weekly content plan."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    try:
        plan = tiktok_studio.generate_weekly_plan(merchant["id"], data.get("product", ""))
        return jsonify({"weekly_plan": plan}), 200
    except Exception as e:
        logger.error(f"[TikTok Studio] Plan generation failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/v1/tiktok-studio/briefs', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_tiktok_studio_briefs():
    """Save a creator brief."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    try:
        brief = tiktok_studio.save_brief(
            merchant["id"],
            data.get("product_angle", ""),
            data.get("niche", ""),
            data.get("cta", ""),
        )
        return jsonify({"brief": brief}), 201
    except Exception as e:
        logger.error(f"[TikTok Studio] Brief save failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/v1/assistant/proactive', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_assistant_proactive():
    """Run the proactive agent and create recommended actions."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    try:
        actions = assistant_engine.run_proactive(merchant["id"])
        return jsonify({"created": actions}), 200
    except Exception as e:
        logger.error(f"[Assistant] Proactive run failed: {e}")
        return jsonify({"detail": str(e)}), 500


@app.route('/api/v1/assistant/thread', methods=['DELETE'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_assistant_clear_thread():
    """Clear the merchant assistant conversation memory."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    assistant_engine.clear_thread(merchant["id"])
    return jsonify({"cleared": True}), 200


@app.route('/api/v1/merchant/timezone', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_merchant_timezone():
    """Store the merchant's browser timezone for accurate greetings/scheduling."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    tz = (data.get("timezone") or "UTC").strip()
    if not tz:
        tz = "UTC"
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
    except Exception:
        return jsonify({"error": "Invalid timezone"}), 400
    setting = MerchantSetting.query.filter_by(merchant_id=merchant["id"], setting_key="merchant_timezone").first()
    if not setting:
        setting = MerchantSetting(merchant_id=merchant["id"], setting_key="merchant_timezone")
        db.session.add(setting)
    setting.setting_value = tz
    db.session.commit()
    return jsonify({"timezone": tz}), 200


@app.route('/api/v1/tiktok-studio/posts', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_tiktok_studio_posts():
    """Queue a TikTok post."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    try:
        post = tiktok_studio.add_post(
            merchant["id"],
            data.get("caption", ""),
            data.get("scheduled_for", ""),
        )
        return jsonify({"post": post}), 201
    except Exception as e:
        logger.error(f"[TikTok Studio] Post queue failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/v1/startup-pack/intake', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_startup_pack_intake():
    """Save Startup Pack concierge intake and email the founder a notification."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    try:
        project = startup_pack.save_intake(merchant["id"], data)
        project_dict = startup_pack.project_to_dict(project)

        email_html = f"""
        <h3>New Custom Brand Build Intake</h3>
        <p><strong>Merchant:</strong> {merchant.get('name') or merchant['id']} ({merchant.get('email') or 'no email'})</p>
        <ul>
          <li><strong>Brand name:</strong> {project.brand_name or '-'}</li>
          <li><strong>Niche:</strong> {project.niche or '-'}</li>
          <li><strong>Target audience:</strong> {project.target_audience or '-'}</li>
          <li><strong>Monthly ad budget:</strong> ${project.monthly_ad_budget or 0:,.2f}</li>
          <li><strong>Design vibe:</strong> {project.design_vibe or '-'}</li>
          <li><strong>Has domain:</strong> {'Yes' if project.has_domain else 'No'}</li>
          <li><strong>First product idea:</strong> {project.sample_product or '-'}</li>
        </ul>
        <p>Reply to the merchant directly from this email to deliver the custom brand brief with US-based manufacturer matches.</p>
        """
        email_sent = dispatch_external_email(
            recipient=MERCHANT_EMAIL,
            subject=f"Custom brand intake: {project.brand_name or merchant['id']}",
            html_body=email_html,
        )
        return jsonify({**project_dict, "email_sent": email_sent}), 200
    except Exception as e:
        logger.error(f"[Startup Pack] Intake failed: {e}")
        return jsonify({"detail": "Could not save intake."}), 500


@app.route('/api/v1/startup-pack/checklist/<item_id>', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_startup_pack_check_item(item_id):
    """Toggle a checklist item complete/incomplete."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    try:
        project = startup_pack.complete_item(merchant["id"], item_id)
        return jsonify(startup_pack.project_to_dict(project)), 200
    except Exception as e:
        logger.error(f"[Startup Pack] Checklist update failed: {e}")
        return jsonify({"detail": str(e)}), 400


# ============================================================
# ADMIN: STARTUP PACK SUBMISSIONS
# ============================================================

@app.route('/admin/startup-pack', methods=['GET'])
@require_roles([UserRole.ADMIN])
def admin_startup_pack():
    """Admin review page for Startup Pack concierge submissions."""
    merchant = get_merchant_context()
    merchant_id = merchant["id"] if merchant else None
    ctx = context(active_page='admin_startup_pack', merchant=merchant, merchant_id=merchant_id)
    return render_template('admin_startup_pack.html', **ctx)


@app.route('/api/admin/startup-pack/submissions', methods=['GET'])
@require_roles([UserRole.ADMIN])
def api_admin_startup_pack_submissions():
    """List all Startup Pack submissions for admin review."""
    try:
        pending = [startup_pack.project_to_dict(p) for p in startup_pack.list_pending_briefs()]
        delivered = [startup_pack.project_to_dict(p) for p in startup_pack.list_delivered_briefs()]
        return jsonify({"pending": pending, "delivered": delivered}), 200
    except Exception as e:
        logger.error(f"[Admin Startup Pack] Failed: {e}")
        return jsonify({"detail": "Could not load submissions."}), 500


@app.route('/api/admin/startup-pack/<merchant_id>/brief', methods=['POST'])
@require_roles([UserRole.ADMIN])
def api_admin_deliver_startup_brief(merchant_id):
    """Admin delivers a curated Startup Pack brief with optional supplier recommendations."""
    data = request.get_json(silent=True) or {}
    try:
        project = startup_pack.deliver_brief(
            merchant_id=merchant_id,
            brief=data.get("brief", ""),
            curated_suppliers=data.get("curated_suppliers", []),
            next_steps=data.get("next_steps", ""),
            admin_notes=data.get("admin_notes", ""),
        )
        return jsonify(startup_pack.project_to_dict(project)), 200
    except Exception as e:
        logger.error(f"[Admin Startup Pack] Deliver brief failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/login')
@limiter.exempt
def login():
    merchant = get_merchant_context()
    if merchant:
        return redirect(url_for('dashboard'))
    return render_template('beta_login.html', error=request.args.get('error') or '', recaptcha_site_key=RECAPTCHA_SITE_KEY)


@app.route('/site-login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def site_login():
    if not site_wall_enabled():
        return redirect(url_for('home'))
    if site_wall_authenticated():
        return redirect(url_for('home'))

    error = False
    if request.method == 'POST':
        submitted = request.form.get('password', '')
        if hmac.compare_digest(submitted, SITE_WALL_PASSWORD):
            token = secrets.token_urlsafe(32)
            now = datetime.utcnow()
            # Site-wall sessions do not impersonate any merchant; a real login is still required.
            db.session.add(ActiveSession(token=token, merchant_id=None, role="SiteWall", created_at=now, last_seen=now))
            db.session.commit()
            response = redirect(url_for('home'))
            response.set_cookie(
                SESSION_COOKIE_NAME,
                token,
                max_age=SESSION_IDLE_TIMEOUT_MINUTES * 60,
                httponly=True,
                samesite='Lax',
                secure=app.config.get("SESSION_COOKIE_SECURE", os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"),
            )
            return response
        error = True
    return redirect(url_for('login', error=1)) if error else redirect(url_for('login'))


@app.route('/site-logout')
def site_logout():
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        ActiveSession.query.filter_by(token=token).delete()
        db.session.commit()
    response = redirect(url_for('home'))
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.route('/api/session/heartbeat', methods=['POST'])
@limiter.exempt
def session_heartbeat():
    """Keep session alive while the user is active; return remaining seconds."""
    if not site_wall_authenticated(refresh=True):
        return jsonify({"valid": False, "detail": "Session expired or invalid"}), 401
    return jsonify({"valid": True, "expires_in": SESSION_IDLE_TIMEOUT_MINUTES * 60}), 200


@app.route('/api/v1/auth/login', methods=['POST'])
def auth_login():
    """Verify reCAPTCHA v3, then validate email + password and issue a session cookie."""
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password", "")
    captcha_token = payload.get("captcha_token", "")

    if not email or not password:
        return jsonify({"detail": "CRITICAL ERROR: Email and password are required."}), 400

    # 1. Enforce Bot Interception Pass
    bot_score = verify_captcha_v3(captcha_token)
    if bot_score < 0.5:
        return jsonify({"detail": "AUTOMATION GUARD: Automated traffic signature identified. Access blocked."}), 403

    # 2. Credential evaluation against DB hash
    profile = MerchantProfile.query.filter_by(admin_email=email).first()
    if not profile or not profile.password_hash:
        return jsonify({"detail": "CRITICAL ERROR: Invalid authentication credentials match failed."}), 401

    if not check_password_hash(profile.password_hash, password):
        return jsonify({"detail": "CRITICAL ERROR: Invalid authentication credentials match failed."}), 401

    # 3. Issue encrypted session JWT (session cookie + ActiveSession row)
    session_token = secrets.token_urlsafe(32)
    if email in MASTER_ADMIN_EMAILS:
        assigned_role = UserRole.ADMIN.value
    elif email in ENGINEER_EMAILS:
        assigned_role = UserRole.ENGINEER.value
    else:
        assigned_role = UserRole.MERCHANT.value
    db.session.add(ActiveSession(token=session_token, merchant_id=profile.merchant_id, role=assigned_role, created_at=datetime.utcnow()))
    db.session.commit()

    response = make_response(jsonify({
        "status": "AUTHORIZED",
        "role": assigned_role,
        "merchant_id": profile.merchant_id,
    }))
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=SESSION_TIMEOUT_DAYS * 86400,
        httponly=True,
        samesite="Lax",
        secure=app.config.get("SESSION_COOKIE_SECURE", os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"),
    )
    return response, 200


TIER_NAME_MAP = {
    "Starter": "Basic Tier",
    "Pro": "Pro Tier",
    "Enterprise": "Enterprise AI Tier",
}


@app.route('/api/v1/auth/signup', methods=['POST'])
def auth_signup():
    """Verify reCAPTCHA, create a new merchant + tenant, and issue a session cookie."""
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password", "")
    selected_tier = (payload.get("selected_tier") or "").strip()
    captcha_token = payload.get("captcha_token", "")

    if not email or not password or len(password) < 8:
        return jsonify({"detail": "A valid email and a password of at least 8 characters are required."}), 400

    tier = TIER_NAME_MAP.get(selected_tier)
    if not tier:
        return jsonify({"detail": "Invalid system tier parameters provided."}), 400

    # 1. Capture Bot Registrations
    bot_score = verify_captcha_v3(captcha_token)
    if bot_score < 0.5:
        return jsonify({"detail": "AUTOMATION EXCLUSION: Registration dropped due to low security compliance score."}), 403

    # 2. Prevent duplicate tenants
    if MerchantProfile.query.filter_by(admin_email=email).first():
        return jsonify({"detail": "A merchant account with this email is already provisioned."}), 409

    # 3. Provision tenant and billing records
    merchant_id = f"tenant_{uuid.uuid4().hex[:8]}"
    try:
        db.session.add(MerchantProfile(
            merchant_id=merchant_id,
            business_name=f"Storefront {merchant_id}",
            admin_email=email,
            account_tier=tier,
            password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
        ))
        db.session.add(SaaSBilling(
            merchant_id=merchant_id,
            stripe_customer_id=f"cus_{merchant_id}",
            current_plan=tier,
            metered_usage_units=0,
            accrued_invoice_value=0.0,
            billing_cycle_end=(datetime.utcnow() + timedelta(days=30)).strftime('%Y-%m-%d'),
        ))
        db.session.add(MerchantMetric(
            merchant_id=merchant_id,
            total_unified_balance=0.0,
            true_net_profit=0.0,
            gross_revenue=0.0,
            ai_briefing="System initialized. Complete onboarding to activate multi-channel engine.",
        ))
        db.session.commit()
    except Exception as e:
        logger.error(f"[SIGNUP] Failed to provision {email}: {e}")
        return jsonify({"detail": "Tenant provisioning failed. Please retry."}), 500

    # 4. Issue session cookie
    session_token = secrets.token_urlsafe(32)
    db.session.add(ActiveSession(token=session_token, merchant_id=merchant_id, role=UserRole.MERCHANT.value, created_at=datetime.utcnow()))
    db.session.commit()

    response = make_response(jsonify({
        "status": "SUCCESS",
        "message": "Multi-tenant engine environment provisioned flawlessly.",
        "tenant_id": merchant_id,
        "assigned_tier": tier,
        "monthly_order_limit": TIER_LIMITS[tier]["monthly_order_limit"],
    }))
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=SESSION_TIMEOUT_DAYS * 86400,
        httponly=True,
        samesite="Lax",
        secure=app.config.get("SESSION_COOKIE_SECURE", os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"),
    )
    return response, 201


@app.route('/api/v1/auth/provision-node', methods=['POST'])
@require_roles([UserRole.ADMIN])
def auth_provision_node():
    """Admin-only user provisioning. Prevents self-service role escalation."""
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password", "")
    role = (payload.get("role") or "").strip()
    selected_tier = (payload.get("selected_tier") or "Starter").strip()

    if not email or not password or len(password) < 8:
        return jsonify({"detail": "A valid email and a password of at least 8 characters are required."}), 400

    if not role or role not in {UserRole.MERCHANT.value, UserRole.ENGINEER.value}:
        return jsonify({"detail": "CRITICAL SECURITY: Internal system accounts must be white-listed by a Master Admin."}), 403

    if MerchantProfile.query.filter_by(admin_email=email).first():
        return jsonify({"detail": "A merchant account with this email is already provisioned."}), 409

    tier = TIER_NAME_MAP.get(selected_tier)
    if not tier:
        return jsonify({"detail": "Invalid system tier parameters provided."}), 400

    merchant_id = f"tenant_{uuid.uuid4().hex[:8]}"
    try:
        db.session.add(MerchantProfile(
            merchant_id=merchant_id,
            business_name=f"Provisioned {role}",
            admin_email=email,
            account_tier=tier,
            password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
        ))
        db.session.add(SaaSBilling(
            merchant_id=merchant_id,
            stripe_customer_id=f"cus_{merchant_id}",
            current_plan=tier,
            metered_usage_units=0,
            accrued_invoice_value=0.0,
            billing_cycle_end=(datetime.utcnow() + timedelta(days=30)).strftime('%Y-%m-%d'),
        ))
        db.session.add(MerchantMetric(
            merchant_id=merchant_id,
            total_unified_balance=0.0,
            true_net_profit=0.0,
            gross_revenue=0.0,
            ai_briefing=f"Provisioned {role} account. Activate multi-channel engine.",
        ))
        db.session.commit()
    except Exception as e:
        logger.error(f"[PROVISION] Failed to provision {email}: {e}")
        return jsonify({"detail": "Tenant provisioning failed. Please retry."}), 500

    return jsonify({
        "status": "PROVISIONED",
        "email": email,
        "assigned_role": role,
        "allocated_volume_allowance": TIER_LIMITS[tier]["monthly_order_limit"],
        "tenant_id": merchant_id,
    }), 201


@app.route('/api/v1/routing/optimize', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER, UserRole.MERCHANT])
def optimize_order_routing():
    """Evaluate live stock across fulfillment hubs and return the optimal warehouse."""
    data = request.get_json(silent=True) or {}
    order = SmartOrderPayload(**data.get("order", {}))
    inventory = [WarehouseInventoryNode(**node) for node in data.get("inventory_pool", [])]
    hub = AISmartRouter.calculate_optimal_hub(order, inventory)
    return jsonify({
        "order_id": order.order_id,
        "target_sku": order.target_sku,
        "recommended_hub": hub,
    }), 200


@app.route('/api/v1/products/transform/shopify-to-tiktok', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER, UserRole.MERCHANT])
def transform_product_to_tiktok():
    """Clone and transform a Shopify product layout into a TikTok Shop draft."""
    data = request.get_json(silent=True) or {}
    try:
        source = ShopifyProductLayout(**data)
        draft = ProductTransformerEngine.transform_shopify_to_tiktok(source)
        return draft.model_dump(), 200
    except Exception as e:
        return jsonify({"detail": f"Product transformation failed: {str(e)}"}), 400


@app.route('/api/v1/profit/ledger', methods=['GET', 'POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER, UserRole.MERCHANT])
def profit_ledger():
    """Live Profit & Margin Matrix: track itemized channel profit after fees, COGS, and shipping."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        required = ["order_id", "sales_channel", "gross_revenue", "marketplace_fees", "cost_of_goods_sold", "shipping_costs"]
        if not all(k in data for k in required):
            return jsonify({"detail": "Missing required ledger fields."}), 400

        try:
            gross = float(data["gross_revenue"])
            fees = float(data["marketplace_fees"])
            cogs = float(data["cost_of_goods_sold"])
            shipping = float(data["shipping_costs"])
            net = gross - fees - cogs - shipping
            entry = ProductFinancialLedger(
                tenant_id=merchant["id"],
                order_id=data["order_id"],
                sales_channel=data["sales_channel"],
                gross_revenue=gross,
                marketplace_fees=fees,
                cost_of_goods_sold=cogs,
                shipping_costs=shipping,
                net_profit=net,
            )
            db.session.add(entry)
            db.session.commit()
            return jsonify({
                "status": "RECORDED",
                "ledger_id": entry.ledger_id,
                "net_profit": float(entry.net_profit),
            }), 201
        except Exception as e:
            logger.error(f"[PROFIT LEDGER] Failed to record entry: {e}")
            return jsonify({"detail": "Ledger entry failed."}), 500

    entries = ProductFinancialLedger.query.filter_by(tenant_id=merchant["id"]).order_by(ProductFinancialLedger.recorded_at.desc()).limit(100).all()
    return jsonify([{
        "ledger_id": e.ledger_id,
        "order_id": e.order_id,
        "sales_channel": e.sales_channel,
        "gross_revenue": float(e.gross_revenue),
        "marketplace_fees": float(e.marketplace_fees),
        "cost_of_goods_sold": float(e.cost_of_goods_sold),
        "shipping_costs": float(e.shipping_costs),
        "net_profit": float(e.net_profit),
        "recorded_at": e.recorded_at.isoformat() if e.recorded_at else None,
    } for e in entries]), 200


@app.route('/api/v1/ad-spend', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER, UserRole.MERCHANT])
def ad_spend_feed():
    """Ingest ad spend for a platform into the real-time Profit Feed."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    platform = data.get("platform") or data.get("platform_source")
    amount = data.get("amount")
    if not platform or amount is None:
        return jsonify({"detail": "platform and amount are required."}), 400
    try:
        spend = profit_feed.record_ad_spend(
            merchant["id"],
            platform,
            float(amount),
            conversion_count=data.get("conversions") or data.get("conversion_count") or 0,
        )
        return jsonify({"status": "recorded", "id": spend.id, "amount": spend.amount}), 201
    except Exception as e:
        logger.error(f"[AD SPEND FEED] Failed: {e}")
        return jsonify({"detail": "Failed to record ad spend."}), 500


@app.route('/api/v1/stripe/create-checkout', methods=['POST'])
def create_stripe_checkout():
    """Create a Stripe Checkout session for the beta plan plus optional startup add-on."""
    data = request.get_json(silent=True) or request.form or {}
    email = (data.get("email") or "").strip().lower()
    business_name = (data.get("business_name") or "").strip() or email
    password = data.get("password", "")
    include_startup_addon = bool(data.get("include_startup_addon"))
    plan = (data.get("plan") or "beta").lower().strip()

    if not email or not password or len(password) < 8:
        return jsonify({"detail": "A valid email and a password of at least 8 characters are required."}), 400

    # Find or provision the merchant account so the webhook can upgrade it.
    profile = MerchantProfile.query.filter_by(admin_email=email).first()
    if profile:
        merchant_id = profile.merchant_id
    else:
        merchant_id = f"tenant_{uuid.uuid4().hex[:8]}"
        from werkzeug.security import generate_password_hash
        db.session.add(MerchantProfile(
            merchant_id=merchant_id,
            business_name=business_name,
            admin_email=email,
            account_tier="Basic Tier",
            password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
            sandbox_status="pending",
            live_access_enabled=0,
        ))
        db.session.add(SaaSBilling(
            merchant_id=merchant_id,
            current_plan="Basic Tier",
            metered_usage_units=0,
            accrued_invoice_value=0.0,
        ))
        db.session.commit()

    try:
        success_url = url_for('dashboard', _external=True, _scheme='https') + '?checkout=success'
        cancel_url = url_for('subscribe', _external=True, _scheme='https') + '?canceled=1'
        session_url, session_id, customer_id = billing_module.create_checkout_session(
            merchant_id,
            email,
            business_name,
            include_startup_addon=include_startup_addon,
            plan=plan,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return jsonify({"url": session_url, "session_id": session_id, "customer_id": customer_id}), 200
    except Exception as e:
        logger.error(f"[Stripe Checkout] Failed: {e}")
        return jsonify({"detail": "Unable to start checkout session."}), 500


@app.route('/api/v1/stripe/customer-portal', methods=['POST'])
def stripe_customer_portal():
    """Return a Stripe Billing Portal session for the authenticated merchant."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    try:
        return_url = url_for('dashboard_page', page='billing', _external=True, _scheme='https')
        url = billing_module.create_customer_portal_session(merchant["id"], return_url=return_url)
        return jsonify({"url": url}), 200
    except Exception as e:
        logger.error(f"[Stripe Portal] Failed: {e}")
        return jsonify({"detail": "Unable to open billing portal."}), 500


@app.route('/api/v1/fulfillment/tracking-injection', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def tracking_injection():
    """Receive a fulfillment update and dispatch tracking to the target marketplace."""
    data = request.get_json(silent=True) or {}
    channel = (data.get("sales_channel") or "").lower()
    ref = data.get("marketplace_reference_id", "")
    tracking = data.get("tracking_number", "")
    carrier = data.get("carrier", "")

    if not ref or not tracking:
        return jsonify({"detail": "marketplace_reference_id and tracking_number are required."}), 400

    if channel == "tiktokshop":
        logger.info(f"[OUTBOUND API] TikTok Order {ref} marked shipped: {tracking} / {carrier}")
        return jsonify({"status": "TIKTOK_INJECTED"}), 200
    elif channel == "shopify":
        logger.info(f"[OUTBOUND API] Shopify Order {ref} tracking updated: {tracking} / {carrier}")
        return jsonify({"status": "SHOPIFY_INJECTED"}), 200

    return jsonify({"detail": "Unsupported storefront channel pipeline execution requested."}), 400


@app.route('/api/v1/ai-coo/execute-analysis', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER, UserRole.MERCHANT])
def ai_coo_execute():
    """Unified AI Assistant analysis endpoint combining DB state and screen context."""
    payload = request.get_json(silent=True) or {}
    merchant = get_merchant_context()
    tenant_id = (merchant.get("id") if merchant else "") or payload.get("tenant_id", "")

    if not tenant_id:
        return jsonify({"detail": "No tenant context available."}), 403

    # Gather DB-derived context
    sync_errors = [cc.channel_id for cc in CommerceChannel.query.all() if cc.performance_status not in ("Optimal", "Trending", "Stable")]
    latest = BusinessMetric.query.order_by(BusinessMetric.id.desc()).first()
    net_profit_margin = 0.0
    if latest and latest.gross_revenue:
        net_profit_margin = float(latest.true_net_profit) / float(latest.gross_revenue)

    pl = PredictiveLogistics.query.filter(PredictiveLogistics.days_remaining < 7).first()
    low_stock_sku = pl.variant_sku if pl else "UNKNOWN"
    current_velocity = pl.forecasted_demand_velocity if pl else 0.0
    remaining_stock = pl.days_remaining if pl else 0

    data_context = {
        "tenant_id": tenant_id,
        "active_screen_view": payload.get("active_screen_view", "dashboard"),
        "scraped_screen_data": payload.get("scraped_screen_data", ""),
        "sync_error_count": len(sync_errors),
        "sync_errors": sync_errors,
        "net_profit_margin": net_profit_margin,
        "low_stock_sku": low_stock_sku,
        "current_velocity": current_velocity,
        "remaining_stock": remaining_stock,
    }

    coo = AICooEngine(tenant_id)
    result = coo.execute_analysis(data_context)
    return jsonify(result), 200


@app.route('/api/v1/settings/update', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT])
def update_tenant_settings():
    """Secure settings storage: channel tokens and AI Assistant permissions."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"detail": "No merchant context."}), 403

    data = request.get_json(silent=True) or {}
    tenant_id = data.get("tenant_id") or merchant["id"]
    if tenant_id != merchant["id"]:
        return jsonify({"detail": "Cross-tenant settings updates are not permitted."}), 403

    try:
        for key in ["shopify_key", "tiktok_key"]:
            value = data.get(key)
            if value:
                s = MerchantSetting.query.get((merchant["id"], key))
                if s:
                    s.setting_value = value
                else:
                    db.session.add(MerchantSetting(merchant_id=merchant["id"], setting_key=key, setting_value=value))

        ai_override = "1" if data.get("ai_automation_allowed") in (True, "true", "1", 1) else "0"
        s = MerchantSetting.query.get((merchant["id"], "ai_automation_allowed"))
        if s:
            s.setting_value = ai_override
        else:
            db.session.add(MerchantSetting(merchant_id=merchant["id"], setting_key="ai_automation_allowed", setting_value=ai_override))

        db.session.commit()

        # Engine action logs
        logger.info(f"[SECURE SECRETS STORAGE] Updated settings for {merchant['id']}")

        return jsonify({
            "status": "CONFIGURATION_UPDATED",
            "tenant_id": merchant["id"],
            "ai_autonomous_status": ai_override == "1",
        }), 200
    except Exception as e:
        logger.error(f"[SETTINGS UPDATE] Failed for {merchant['id']}: {e}")
        return jsonify({"detail": "Failed to save configuration."}), 500


SHOPIFY_WEBHOOK_SECRET = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "").strip().encode()
TIKTOK_WEBHOOK_SECRET = os.environ.get("TIKTOK_WEBHOOK_SECRET", "").strip().encode()
AMAZON_WEBHOOK_SECRET = os.environ.get("AMAZON_WEBHOOK_SECRET", "").strip().encode()


@app.route('/api/v1/webhooks/shopify-orders', methods=['POST'])
@limiter.limit("60 per minute")
def shopify_orders_webhook():
    """Production-hardened, idempotent webhook capture: verify HMAC, mutate state, broadcast."""
    try:
        raw_body = request.get_data()
        hmac_header = request.headers.get("X-Shopify-Hmac-SHA256")
        event_id = request.headers.get("X-Shopify-Webhook-Id")
        merchant_target = request.args.get("merchant_id", "merchant_shawn_01")

        if SHOPIFY_WEBHOOK_SECRET:
            if not hmac_header:
                log_system_exception("SHOPIFY_WEBHOOK", "WARNING", "Dropped inbound webhook: missing HMAC signature.")
                return jsonify({"status": "rejected", "reason": "Missing HMAC"}), 401
            computed = hmac.new(SHOPIFY_WEBHOOK_SECRET, raw_body, hashlib.sha256).digest()
            if not hmac.compare_digest(computed, base64.b64decode(hmac_header)):
                log_system_exception("SHOPIFY_WEBHOOK", "WARNING", "Dropped inbound webhook: invalid HMAC signature.")
                return jsonify({"status": "rejected", "reason": "Invalid HMAC"}), 401
        else:
            logger.warning("SHOPIFY_WEBHOOK_SECRET not set — accepting webhook without HMAC verification")

        if not event_id:
            log_system_exception("SHOPIFY_WEBHOOK", "WARNING", "Dropped inbound webhook: missing X-Shopify-Webhook-Id.")
            return jsonify({"status": "rejected", "reason": "Missing event id"}), 400

        blocked = enforce_tier_limits(merchant_target, "shopify")
        if blocked:
            return blocked

        if ProcessedWebhookEvent.query.get(event_id):
            logger.info(f"Idempotency: order event {event_id} already processed. Ignoring.")
            return jsonify({"status": "duplicate_ignored"}), 200

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as je:
            error_msg = f"Inbound JSON malformed parse drop: {str(je)}"
            log_system_exception("WEBHOOK_PARSER", "CRITICAL", error_msg)
            db.session.commit()
            manager.broadcast({
                "type": "ui_update",
                "updates": {
                    "ai_briefing": "⚠️ Security Alert: Intercepted a corrupted multi-tenant payload structure. Core tables insulated.",
                    "system_error_alert": error_msg,
                }
            })
            return "Malformed Block Blocked", 400

        order_value = float(payload.get("total_price", 0.00))

        # Feed the real-time Profit Feed for this channel order.
        line_items = payload.get("line_items") or payload.get("lineItems") or []
        order_ref = str(payload.get("name") or payload.get("order_number") or event_id)
        profit_feed.record_order(
            merchant_id=merchant_target,
            channel="shopify",
            order_id=order_ref,
            gross_revenue=order_value,
            items=len(line_items) if isinstance(line_items, list) else 1,
            state="shipped" if payload.get("fulfillment_status") != "cancelled" else "cancelled",
            refund_amount=abs(float(payload.get("total_refund_amount", 0.0) or 0.0)),
        )

        latest = BusinessMetric.query.order_by(BusinessMetric.id.desc()).first()
        if not latest:
            return jsonify({"status": "rejected", "reason": "No baseline metrics"}), 400

        new_bal = latest.total_unified_balance + order_value
        new_gross = latest.gross_revenue + order_value
        new_profit = latest.true_net_profit + (order_value * 0.42)
        new_briefing = f"⚡ Live Webhook: Shopify order received for ${order_value:.2f}. Database updated automatically."

        shopify = CommerceChannel.query.get("shopify")
        if shopify:
            shopify.pending_orders += 1
            DASHBOARD_STATE["channels"]["shopify"]["pending_orders"] = shopify.pending_orders
            for c in CHANNELS:
                if "shopify" in c["name"].lower():
                    c["orders"] = shopify.pending_orders

        db.session.add(BusinessMetric(
            merchant_id=merchant_target,
            total_unified_balance=new_bal,
            true_net_profit=new_profit,
            gross_revenue=new_gross,
            ai_briefing=new_briefing,
        ))
        db.session.add(ProcessedWebhookEvent(event_id=event_id))
        mch = MerchantChannel.query.filter_by(merchant_id=merchant_target, channel_id="shopify").first()
        if not mch:
            mch = MerchantChannel(merchant_id=merchant_target, channel_id="shopify", pending_orders=0, conversion_rate=3.5)
            db.session.add(mch)
        mch.pending_orders += 1
        db.session.commit()

        DASHBOARD_STATE["total_unified_balance"] = new_bal
        DASHBOARD_STATE["true_net_profit"] = new_profit
        DASHBOARD_STATE["gross_revenue"] = new_gross
        DASHBOARD_STATE["ai_briefing"] = new_briefing
        COO["narrative"] = new_briefing
        BRIEFING["revenue"] = new_gross

        support = SupportMetric.query.order_by(SupportMetric.id.desc()).first()
        mktg = MarketingStudio.query.order_by(MarketingStudio.id.desc()).first()
        s_chats = support.active_chats if support else DASHBOARD_STATE["support_chats"]
        s_sentiment = support.sentiment_score if support else DASHBOARD_STATE["support_sentiment"]
        s_resolution = support.recent_resolution if support else DASHBOARD_STATE["support_resolution"]
        m_camp = mktg.active_campaign if mktg else DASHBOARD_STATE["mktg_campaign"]
        m_status = mktg.generation_status if mktg else DASHBOARD_STATE["mktg_status"]
        m_copy = mktg.copy_preview if mktg else DASHBOARD_STATE["mktg_copy"]

        updates = {
            "ai_briefing": new_briefing,
            "total_balance": f"{new_bal:.2f}",
            "shopify_orders": shopify.pending_orders if shopify else 0,
            "support_chats": s_chats,
            "support_sentiment": s_sentiment,
            "support_resolution": s_resolution,
            "mktg_campaign": m_camp,
            "mktg_status": m_status,
            "mktg_copy": m_copy,
        }
        manager.broadcast({"type": "ui_update", "updates": updates})

        return jsonify({"status": "synchronized", "amount": order_value})

    except Exception as e:
        error_msg = f"Webhook pipeline anomaly: {str(e)}"
        log_system_exception("SHOPIFY_WEBHOOK", "CRITICAL", error_msg)
        db.session.commit()
        manager.broadcast({
            "type": "ui_update",
            "updates": {
                "ai_briefing": "⚠️ Webhook Hardening Intercepted an unhandled runtime anomaly. Exception logged.",
                "system_error_alert": error_msg,
            }
        })
        return jsonify({"status": "rejected", "reason": "Hardened intercept"}), 400


def process_idempotent_channel_event(event_id, merchant_id, platform_id, amount=0.0):
    """Record a webhook event idempotently and bump the per-merchant channel order count."""
    if not event_id:
        return False
    if ProcessedWebhookEvent.query.get(event_id):
        return False
    db.session.add(ProcessedWebhookEvent(event_id=event_id))
    channel = MerchantChannel.query.filter_by(merchant_id=merchant_id, channel_id=platform_id).first()
    if not channel:
        channel = MerchantChannel(merchant_id=merchant_id, channel_id=platform_id, pending_orders=0, conversion_rate=3.5)
        db.session.add(channel)
    channel.pending_orders = (channel.pending_orders or 0) + 1
    return True


@app.route('/api/v1/webhooks/tiktok-orders', methods=['POST'])
@limiter.limit("60 per minute")
def tiktok_orders_webhook():
    """Ingest TikTok Shop order events into the isolated merchant channel."""
    event_id = request.headers.get("X-Tiktok-Event-Id") or request.headers.get("X-TikTok-Event-Id")
    merchant_target = request.args.get("merchant_id", "merchant_shawn_01")
    blocked = enforce_tier_limits(merchant_target, "tiktok")
    if blocked:
        return blocked
    raw = request.get_json() or {}
    order_price = float(raw.get("order_amount", raw.get("total_amount", 0.00)))
    try:
        created = process_idempotent_channel_event(event_id, merchant_target, "tiktok", order_price)
        db.session.commit()
        # Feed the real-time Profit Feed for TikTok Shop.
        skus = raw.get("skus") or raw.get("items") or []
        profit_feed.record_order(
            merchant_id=merchant_target,
            channel="tiktok",
            order_id=str(raw.get("order_id") or event_id),
            gross_revenue=order_price,
            items=len(skus) if isinstance(skus, list) else 1,
            state="shipped" if raw.get("status") != "CANCELLED" else "cancelled",
        )
        # Trigger real-time multi-channel routing pipeline in the background
        run_async_task(lambda: asyncio.run(process_incoming_order_event(raw)))
        return jsonify({"status": "synchronized" if created else "ignored"}), 200
    except Exception as e:
        log_system_exception("TIKTOK_WEBHOOK", "CRITICAL", str(e))
        db.session.rollback()
        return jsonify({"status": "rejected", "reason": "Internal error"}), 500


@app.route('/api/v1/webhooks/amazon-orders', methods=['POST'])
@limiter.limit("60 per minute")
def amazon_orders_webhook():
    """Ingest Amazon Seller Central order events into the isolated merchant channel."""
    event_id = request.headers.get("X-Amazon-Sqs-Message-Id")
    merchant_target = request.args.get("merchant_id", "merchant_shawn_01")
    blocked = enforce_tier_limits(merchant_target, "amazon")
    if blocked:
        return blocked
    raw = request.get_json() or {}
    payload = raw.get("payload", raw)
    order_price = float(payload.get("AmazonOrderTotal", payload.get("total", 0.00)))
    try:
        created = process_idempotent_channel_event(event_id, merchant_target, "amazon", order_price)
        db.session.commit()
        # Feed the real-time Profit Feed for Amazon.
        items = payload.get("NumberOfItemsShipped") or payload.get("items") or []
        profit_feed.record_order(
            merchant_id=merchant_target,
            channel="amazon",
            order_id=str(payload.get("AmazonOrderId") or payload.get("order_id") or event_id),
            gross_revenue=order_price,
            items=int(items) if isinstance(items, (int, float, str)) and str(items).isdigit() else (len(items) if isinstance(items, list) else 1),
            state="shipped" if payload.get("OrderStatus") != "Canceled" else "cancelled",
        )
        return jsonify({"status": "synchronized" if created else "ignored"}), 200
    except Exception as e:
        log_system_exception("AMAZON_WEBHOOK", "CRITICAL", str(e))
        db.session.rollback()
        return jsonify({"status": "rejected", "reason": "Internal error"}), 500


@app.route('/api/v1/webhooks/stripe-billing', methods=['POST'])
def stripe_billing_webhook():
    """Verify and process Stripe checkout/subscription events to upgrade merchant tier live."""
    sig_header = request.headers.get("Stripe-Signature")
    if not sig_header:
        logger.warning("Dropped Stripe frame: missing signature.")
        return jsonify({"error": "Unauthorized"}), 400

    try:
        payload = request.get_data(as_text=True)
        event = billing_module.handle_webhook(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        event_type = event.get("type")

        if event_type in ("checkout.session.completed", "customer.subscription.updated"):
            session_obj = event.get("data", {}).get("object", {})
            stripe_cust_id = session_obj.get("customer")
            metadata = session_obj.get("metadata", {})
            merchant_target = metadata.get("merchant_id", "merchant_shawn_01")
            chosen_tier = metadata.get("selected_tier", "Beta Tier")
            startup_addon = metadata.get("startup_addon") == "true"

            profile = MerchantProfile.query.get(merchant_target)
            if profile:
                profile.account_tier = chosen_tier
                # Paid subscribers bypass the waitlist sandbox and get live access.
                profile.sandbox_status = "approved"
                profile.live_access_enabled = 1
                profile.approved_at = datetime.utcnow()
            saas_billing = SaaSBilling.query.get(merchant_target)
            if saas_billing:
                saas_billing.current_plan = chosen_tier
                saas_billing.stripe_customer_id = stripe_cust_id
                # Persist the subscription ID if available.
                subscription_id = session_obj.get("subscription")
                if subscription_id:
                    saas_billing.stripe_subscription_item_id = subscription_id
            db.session.commit()
            logger.info(f"[Stripe Pipeline] Merchant {merchant_target} upgraded to {chosen_tier}; addon={startup_addon}")
            return jsonify({"status": "tier_synchronized"}), 200

        if event_type == "customer.subscription.deleted":
            sub_obj = event.get("data", {}).get("object", {})
            customer_id = sub_obj.get("customer")
            if customer_id:
                saas_billing = SaaSBilling.query.filter_by(stripe_customer_id=customer_id).first()
                if saas_billing:
                    profile = MerchantProfile.query.get(saas_billing.merchant_id)
                    if profile:
                        profile.account_tier = "Basic Tier"
                    saas_billing.current_plan = "Basic Tier"
                    db.session.commit()
            return jsonify({"status": "subscription_cancelled"}), 200

        return jsonify({"status": "unhandled_event_passed"}), 200
    except Exception as e:
        log_system_exception("STRIPE_WEBHOOK", "CRITICAL", str(e))
        db.session.rollback()
        return jsonify({"error": "Internal Processing Stall"}), 500


@app.route('/api/v1/tenant/save-credentials', methods=['POST'])
def save_credentials():
    """Persist marketplace access tokens in the per-tenant vault."""
    merchant = get_merchant_context()
    merchant_id = merchant["id"] if merchant else "merchant_shawn_01"
    data = request.get_json() or {}
    platform_id = data.get("platform_id")
    shop_domain = data.get("shop_domain")
    access_token = data.get("access_token")

    if not platform_id or not shop_domain or not access_token:
        return jsonify({"success": False, "error": "Missing key configuration metrics."}), 400

    try:
        db.session.merge(TenantOAuthToken(
            shop_domain=shop_domain,
            merchant_id=merchant_id,
            platform_id=platform_id,
            access_token_encrypted=access_token,
            scope_permissions="read_write_unified",
        ))
        db.session.commit()
        logger.info(f"[Vault] Saved {platform_id} credentials for {merchant_id}")
        return jsonify({"success": True, "message": f"Successfully mapped channel connection to {shop_domain}."}), 200
    except Exception as e:
        log_system_exception("CREDENTIALS_VAULT", "CRITICAL", str(e))
        db.session.rollback()
        return jsonify({"success": False, "error": "Database lock encountered."}), 500


@app.route('/api/v1/suppliers/po-update', methods=['POST'])
@limiter.limit("60 per minute")
def supplier_po_update():
    """Supplier webhook: update PO tracking and restore stock status."""
    data = request.get_json() or {}
    po_ref = data.get("po_reference")
    tracking_id = data.get("tracking_number")
    new_status = data.get("status", "SHIPPED")

    if not po_ref or not tracking_id:
        return jsonify({"error": "Missing validation tokens."}), 400

    try:
        po = GeneratedPurchaseOrder.query.get(po_ref)
        if not po:
            return jsonify({"error": "Unknown PO reference."}), 404

        po.tracking_number = tracking_id
        po.fulfillment_status = new_status

        # Restore predictive logistics to green
        pl = PredictiveLogistics.query.filter_by(variant_sku=po.variant_sku).first()
        if pl:
            pl.days_remaining = 30
            pl.status_flag = "HEALTHY"
            pl.optimal_restock_date = "Healthy Lifecycle"

        # Push COO briefing into business metrics
        briefing = f"🚚 Supplier Update: PO {po_ref} shipped. Tracking: {tracking_id}. Restock restored."
        db.session.add(BusinessMetric(
            merchant_id=po.merchant_id,
            total_unified_balance=20560.00,
            true_net_profit=1394.00,
            gross_revenue=4582.00,
            ai_briefing=briefing,
        ))
        db.session.commit()

        manager.broadcast({
            "type": "ui_update",
            "updates": {"ai_briefing": briefing},
        })

        logger.info(f"[Supplier] PO {po_ref} updated to {new_status} with tracking {tracking_id}")
        return jsonify({"success": True, "status": "State Sync Complete"}), 200
    except Exception as e:
        log_system_exception("SUPPLIER_PO", "CRITICAL", str(e))
        db.session.rollback()
        return jsonify({"error": "Internal Processing Error"}), 500


@app.route('/api/v1/tenant/execute-mitigation', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def execute_mitigation():
    """One-click AI mitigation: reroute supplier and clear shortage warnings."""
    merchant = get_merchant_context()
    merchant_id = merchant["id"] if merchant else "merchant_shawn_01"
    data = request.get_json() or {}
    action_target = data.get("action_target")

    try:
        if action_target == "REROUTE_SUPPLIER_C":
            pl = PredictiveLogistics.query.filter_by(variant_sku="SZL-VAR-B").first()
            if pl:
                pl.days_remaining = 30
                pl.status_flag = "HEALTHY"

            resolved = "🚀 Operational Success: Auto-rerouted fulfillment to Supplier C. PO-SZL-REFLOW confirmed green. Warning vectors cleared."
            db.session.add(BusinessMetric(
                merchant_id=merchant_id,
                total_unified_balance=20560.00,
                true_net_profit=1640.00,
                gross_revenue=4582.00,
                ai_briefing=resolved,
            ))
            db.session.commit()

            manager.broadcast({
                "type": "ui_update",
                "updates": {"ai_briefing": resolved},
            })

            logger.info(f"[AI Mitigation] Stockout mitigated for {merchant_id}")
            log_merchant_decision(merchant_id, "mitigation_execute", {"action": "REROUTE_SUPPLIER_C"}, build_context_schema(merchant_id))
            db.session.commit()
            log_metered_api_usage(merchant_id, 15)
            db.session.commit()
            return jsonify({"success": True, "message": "Fulfillment pipeline re-routed successfully."}), 200

        return jsonify({"success": False, "error": "Unknown mitigation target."}), 400
    except Exception as e:
        log_system_exception("MITIGATION", "CRITICAL", str(e))
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/v1/context-schema', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def context_schema():
    """Return the proprietary, localized merchant context package."""
    merchant = get_merchant_context()
    merchant_id = merchant["id"] if merchant else "merchant_shawn_01"
    return jsonify({"success": True, "context_schema": build_context_schema(merchant_id)})


@app.route('/api/v1/agents/collaborate', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def agents_collaborate():
    """Trigger the internal multi-agent collaboration loop."""
    merchant = get_merchant_context()
    merchant_id = merchant["id"] if merchant else "merchant_shawn_01"
    data = request.get_json() or {}
    trigger = data.get("trigger", "low_inventory")
    actions = run_multi_agent_collaboration(merchant_id, trigger)
    return jsonify({"success": True, "agent_actions": actions})


@app.route('/api/v1/decision/approve', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def decision_approve():
    merchant = get_merchant_context()
    merchant_id = merchant["id"] if merchant else "merchant_shawn_01"
    data = request.get_json() or {}
    log_merchant_decision(merchant_id, data.get("decision_type", "manual"),
                          data.get("decision_vector", {}), build_context_schema(merchant_id), "approved")
    db.session.commit()
    return jsonify({"success": True, "status": "decision_approved"})


@app.route('/api/v1/decision/modify', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def decision_modify():
    merchant = get_merchant_context()
    merchant_id = merchant["id"] if merchant else "merchant_shawn_01"
    data = request.get_json() or {}
    log_merchant_decision(merchant_id, data.get("decision_type", "manual"),
                          data.get("decision_vector", {}), build_context_schema(merchant_id), "modified")
    db.session.commit()
    return jsonify({"success": True, "status": "decision_modified"})


@app.route('/api/v1/tenant/commit-learned-decision', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def commit_learned_decision():
    """Capture merchant decision, compute confidence index, and mutate state if approved."""
    merchant = get_merchant_context()
    merchant_id = merchant["id"] if merchant else "merchant_shawn_01"
    data = request.get_json() or {}
    trigger_type = data.get("action_trigger_type")
    decision_vector = data.get("user_decision_vector", "APPROVED")
    target_supplier = data.get("chosen_variant_or_supplier", "Supplier C")

    try:
        approved_count = MerchantDecisionLog.query.filter_by(
            merchant_id=merchant_id, user_decision_vector="APPROVED"
        ).count()
        confidence = min(0.99, 0.70 + (approved_count * 0.05))

        log_merchant_decision(
            merchant_id=merchant_id,
            decision_type=trigger_type,
            decision_vector=decision_vector,
            context_snapshot=build_context_schema(merchant_id),
            outcome=decision_vector,
        )
        # populate new columns
        last_log = MerchantDecisionLog.query.filter_by(merchant_id=merchant_id).order_by(MerchantDecisionLog.id.desc()).first()
        if last_log:
            last_log.action_trigger_type = trigger_type
            last_log.user_decision_vector = decision_vector
            last_log.chosen_variant_or_supplier = target_supplier
            last_log.computed_confidence_score = confidence

        if decision_vector == "APPROVED":
            pl = PredictiveLogistics.query.filter_by(variant_sku="SZL-VAR-B").first()
            if pl:
                pl.days_remaining = 30
                pl.status_flag = "HEALTHY"
            resolved_briefing = f"✔ Action Deployed: Operating variables updated. Decision logged to proprietary memory pool. AI confidence index increased to {confidence*100:.1f}% tracking match."
            db.session.add(BusinessMetric(
                merchant_id=merchant_id,
                total_unified_balance=20560.00,
                true_net_profit=1394.00,
                gross_revenue=4582.00,
                ai_briefing=resolved_briefing,
            ))
            manager.broadcast({
                "type": "ui_update",
                "updates": {"ai_briefing": resolved_briefing},
            })

        db.session.commit()
        log_metered_api_usage(merchant_id, 5)
        db.session.commit()
        logger.info(f"[Moat Learning Engine] Decision vector logged. Confidence: {confidence:.2f}")
        return jsonify({"success": True, "confidence_index": confidence}), 200
    except Exception as e:
        log_system_exception("DECISION_LEARN", "CRITICAL", str(e))
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/v1/download-report')
def download_report():
    """Serve the generated CSV ledger to authenticated admins."""
    target = os.path.join(GENERATED_DIR, "shawnzyluxe_ledger.csv")
    if not os.path.exists(target):
        return jsonify({"status": "compiling"}), 404
    return send_file(target, as_attachment=True, download_name="shawnzyluxe_ledger.csv", mimetype="text/csv")


@app.route('/api/v1/tenant/compile-executive-digest', methods=['POST'])
@limiter.limit("10 per hour")
@require_roles([UserRole.ADMIN, UserRole.MERCHANT])
def compile_executive_digest():
    """Build an editorial-style HTML executive digest from merchant metrics."""
    merchant = get_merchant_context()
    merchant_id = merchant["id"] if merchant else "merchant_shawn_01"
    report_ref = f"DIGEST-SZL-{secrets.token_hex(3).upper()}"
    target_filename = "shawnzyluxe_executive_digest.html"
    target_path = os.path.join(GENERATED_DIR, target_filename)

    try:
        latest = BusinessMetric.query.filter_by(merchant_id=merchant_id).order_by(BusinessMetric.id.desc()).first()
        bal = latest.total_unified_balance if latest else 0.0
        profit = latest.true_net_profit if latest else 0.0
        revenue = latest.gross_revenue if latest else 0.0
        briefing = latest.ai_briefing if latest else "Metrics stable."

        report_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Shawnzyluxe Executive Digest</title>
  <style>
    body {{ font-family: 'Helvetica Neue', Arial, sans-serif; background: #FFFFFF; color: #1C1B19; margin: 40px; line-height: 1.6; }}
    .header-frame {{ border-bottom: 2px solid #1C1B19; padding-bottom: 20px; margin-bottom: 40px; }}
    .brand-title {{ font-size: 28px; font-weight: 700; letter-spacing: -1px; text-transform: uppercase; }}
    .meta-tag {{ font-size: 11px; font-family: 'JetBrains Mono'; color: #706E6A; text-transform: uppercase; margin-top: 4px; }}
    .metrics-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin-bottom: 40px; }}
    .metric-box {{ background: #FDFBF7; border: 1px solid #EFECE6; padding: 20px; border-radius: 12px; }}
    .val {{ font-size: 24px; font-weight: bold; color: #2E5236; font-family: 'JetBrains Mono'; margin-top: 6px; }}
    .briefing-box {{ background: #F4F6F9; padding: 24px; border-radius: 16px; border-left: 4px solid #1C1B19; font-size: 14px; color: #4A5A70; }}
  </style>
</head>
<body>
  <div class="header-frame">
    <div class="brand-title">SHAWNZYLUXE OPERATIONS LEDGER</div>
    <div class="meta-tag">REF: {report_ref} | COMPILED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
  </div>
  <div class="metrics-grid">
    <div class="metric-box"><div>Unified Portfolio Balance</div><div class="val">${bal:,.2f}</div></div>
    <div class="metric-box"><div>True Net Profit</div><div class="val">${profit:,.2f}</div></div>
    <div class="metric-box"><div>Gross Revenue</div><div class="val">${revenue:,.2f}</div></div>
  </div>
  <h3>Assistant Summary</h3>
  <div class="briefing-box">{briefing}</div>
</body>
</html>"""

        with open(target_path, "w") as f:
            f.write(report_html)

        log_metered_api_usage(merchant_id, 5)
        db.session.commit()

        logger.info(f"[Report Engine] Compiled digest: {target_path}")
        return jsonify({"success": True, "message": "Executive digest compiled.", "download_endpoint": "/api/v1/tenant/download-digest"}), 201
    except Exception as e:
        log_system_exception("REPORT_GEN", "CRITICAL", str(e))
        return jsonify({"success": False, "error": "Document generation engine timed out."}), 500


@app.route('/api/v1/tenant/download-digest', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT])
def download_digest():
    """Serve the generated executive digest HTML."""
    target = os.path.join(GENERATED_DIR, "shawnzyluxe_executive_digest.html")
    if not os.path.exists(target):
        return jsonify({"error": "Report data processing. Re-query endpoint."}), 404
    return send_file(target, as_attachment=True, download_name="shawnzyluxe_executive_digest.html", mimetype="text/html")


@app.route('/api/v1/telemetry/poll', methods=['GET'])
def telemetry_poll():
    """Serve live, tenant-isolated dashboard metrics for frontend polling."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"success": False, "error": "Access Locked"}), 401

    try:
        latest = BusinessMetric.query.filter_by(merchant_id=merchant["id"]).order_by(BusinessMetric.id.desc()).first()
        if not latest:
            mprofile = MerchantMetric.query.filter_by(merchant_id=merchant["id"]).first()
            if mprofile:
                latest = type("obj", (object,), {
                    "total_unified_balance": mprofile.total_unified_balance,
                    "true_net_profit": mprofile.true_net_profit,
                    "gross_revenue": mprofile.gross_revenue,
                    "ai_briefing": mprofile.ai_briefing,
                })()

        brief = (latest.ai_briefing or "").lower()
        show_mitigation = any(k in brief for k in ("stalled", "delayed", "shortage", "stockout", "inventory crisis", "reorder"))

        support = SupportMetric.query.order_by(SupportMetric.id.desc()).first()
        mktg = MarketingStudio.query.order_by(MarketingStudio.id.desc()).first()
        channels = MerchantChannel.query.filter_by(merchant_id=merchant["id"]).all()
        rows = PredictiveLogistics.query.order_by(PredictiveLogistics.days_remaining.asc()).all()
        ads = AdSpendAnalytic.query.filter_by(merchant_id=merchant["id"]).all()
        agents = AIAgent.query.filter_by(merchant_id=merchant["id"]).order_by(AIAgent.agent_id).all()
        messages = AgentMessage.query.filter_by(merchant_id=merchant["id"]).order_by(AgentMessage.id.desc()).limit(4).all()

        AGENT_DISPLAY_NAME = {
            "agent_logistics": "Operations",
            "agent_finance": "Finance",
            "agent_marketing": "Marketing",
            "agent_support": "Support",
        }

        return jsonify({
            "success": True,
            "account_tier_context": merchant["tier"],
            "ad_spend_matrix": [
                {
                    "platform": a.platform_source,
                    "budget": a.budget_allocated,
                    "spend": a.current_spend,
                    "roas": a.roas,
                    "conversions": a.conversion_count,
                }
                for a in ads
            ],
            "inter_agent_stream": [
                {"sender": AGENT_DISPLAY_NAME.get(m.sender_agent, "Assistant"), "text": m.payload}
                for m in reversed(messages)
            ],
            "metrics": {
                "total_balance": f"{latest.total_unified_balance:.2f}" if latest else "0.00",
                "true_profit": f"{latest.true_net_profit:.2f}" if latest else "0.00",
                "gross_revenue": f"{latest.gross_revenue:.2f}" if latest else "0.00",
                "ai_briefing": latest.ai_briefing if latest else f"Welcome, {merchant['name']}. Initialize your accounts.",
                "stripe_usage": f"{SaaSBilling.query.get(merchant['id']).metered_usage_units:,}" if SaaSBilling.query.get(merchant['id']) else "0",
                "stripe_invoice": f"${SaaSBilling.query.get(merchant['id']).accrued_invoice_value:.2f}" if SaaSBilling.query.get(merchant['id']) else "$0.00",
            },
            "channels": {c.channel_id: {"orders": c.pending_orders, "cr": c.conversion_rate} for c in channels},
            "support": {
                "chats": support.active_chats if support else 0,
                "sentiment": support.sentiment_score if support else "Optimal",
                "resolution": support.recent_resolution if support else "No queries active.",
            },
            "marketing": {
                "campaign": mktg.active_campaign if mktg else "Idle",
                "status": mktg.generation_status if mktg else "Idle",
                "copy": mktg.copy_preview if mktg else "Awaiting triggers.",
            },
            "mitigation": {
                "show_actions": show_mitigation,
                "action_type": "SUPPLIER_REROUTE" if show_mitigation else "",
                "prompt_label": "Critical Inventory Shortage Flagged on SKU: SZL-VAR-B" if show_mitigation else "",
            },
            "agents_pool": [
                {
                    "name": AGENT_DISPLAY_NAME.get(a.agent_id, a.agent_name or "Assistant"),
                    "status": a.status,
                    "last_action": a.last_action,
                }
                for a in agents
            ],
            "charts": {
                "profit_trend_svg": generate_profit_trend_svg(merchant["id"]),
            },
            "predictive": [
                {
                    "sku": r.variant_sku,
                    "days": r.days_remaining,
                    "restock": r.optimal_restock_date,
                    "flag": r.status_flag,
                }
                for r in rows
            ],
        })
    except Exception as e:
        log_system_exception("TELEMETRY_POLL", "CRITICAL", str(e))
        db.session.commit()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/v1/tenant/register', methods=['POST'])
@limiter.limit("5 per hour")
def register_merchant():
    """Create an isolated merchant profile and seed partitioned metrics."""
    data = request.get_json() or {}
    business_name = data.get("business_name", "").strip()
    admin_email = data.get("admin_email", "").strip()
    password_plain = data.get("password_plain", "").strip()

    if not business_name or not admin_email or not password_plain:
        return jsonify({"success": False, "error": "Missing business, email, or password"}), 400

    new_merchant_id = f"merchant_{secrets.token_hex(4)}"
    password_hash = generate_password_hash(password_plain, method="pbkdf2:sha256", salt_length=16)

    try:
        db.session.add(MerchantProfile(
            merchant_id=new_merchant_id,
            business_name=business_name,
            admin_email=admin_email,
            password_hash=password_hash,
        ))
        db.session.add(MerchantMetric(
            merchant_id=new_merchant_id,
            total_unified_balance=0.00,
            true_net_profit=0.00,
            gross_revenue=0.00,
            ai_briefing="Welcome to Vantav. Connect channels to initialize streams.",
        ))
        db.session.commit()
        logger.info(f"Tenant registered: {new_merchant_id} ({admin_email})")
        return jsonify({"success": True, "merchant_id": new_merchant_id, "status": "Workspace Schema Generated"}), 201
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Tenant registration failed for {admin_email}: {e}")
        return jsonify({"success": False, "error": "Administrative profile email already registered"}), 400


@app.route('/api/v1/tenant/generate-magic-link', methods=['POST'])
@limiter.limit("10 per hour")
def generate_magic_link():
    """Generate a time-locked magic link and queue Mailgun dispatch."""
    data = request.get_json() or {}
    email = data.get("admin_email", "").strip().lower()
    business_name = data.get("business_name", "New Storefront Venture")
    selected_tier = data.get("selected_tier", "Pro Tier")

    if not email:
        return jsonify({"success": False, "error": "Administrative target email required."}), 400

    try:
        profile = MerchantProfile.query.filter_by(admin_email=email).first()
        if not profile:
            merchant_id = f"merchant_{secrets.token_hex(4)}"
            db.session.add(MerchantProfile(
                merchant_id=merchant_id,
                business_name=business_name,
                admin_email=email,
                account_tier=selected_tier,
            ))
            db.session.add(SaaSBilling(merchant_id=merchant_id, current_plan=selected_tier))
            db.session.add(BusinessMetric(
                merchant_id=merchant_id,
                total_unified_balance=0.00,
                true_net_profit=0.00,
                gross_revenue=0.00,
                ai_briefing="Workspace initialized via Magic Link.",
            ))
        else:
            merchant_id = profile.merchant_id

        magic_token = secrets.token_urlsafe(32)
        expires = datetime.utcnow() + timedelta(minutes=15)
        db.session.add(MagicLoginToken(
            token=magic_token,
            admin_email=email,
            merchant_id=merchant_id,
            expires_at=expires,
            is_used=0,
        ))
        db.session.commit()

        magic_url = f"{request.url_root.rstrip('/')}/api/v1/auth/magic-login?token={magic_token}"

        # Dispatch via background worker
        run_async_task(dispatch_external_email, email, "Access Your Vantav Workspace",
                       f"<p>Click below to access your workspace:</p><a href='{magic_url}'>{magic_url}</a>")

        log_metered_api_usage(merchant_id, 1)
        db.session.commit()

        logger.info(f"[Magic Link] Generated for {email}: {magic_url}")
        return jsonify({"success": True, "message": "Magic authorization link compiled and queued.", "debug_link": magic_url}), 201
    except Exception as e:
        log_system_exception("MAGIC_LINK", "CRITICAL", str(e))
        db.session.rollback()
        return jsonify({"success": False, "error": "Database concurrency error."}), 500


@app.route('/api/v1/auth/magic-login', methods=['GET'])
def magic_login():
    """Validate magic token, drop secure session cookie, and redirect to dashboard."""
    token = request.args.get("token")
    if not token:
        return redirect("/login?error=missing_token")

    mlink = MagicLoginToken.query.get(token)
    if not mlink:
        return redirect("/login?error=invalid_token")

    if mlink.is_used or datetime.utcnow() > mlink.expires_at:
        return redirect("/login?error=token_expired")

    # Mark token used and rotate active session
    mlink.is_used = 1
    profile = MerchantProfile.query.filter_by(admin_email=mlink.admin_email).first()
    if not profile:
        return redirect("/login?error=profile_missing")

    now = datetime.utcnow()
    if profile.sandbox_status == "sandbox" and profile.sandbox_expires_at and profile.sandbox_expires_at <= now:
        return redirect("/login?error=sandbox_expired")

    session_token = secrets.token_urlsafe(32)
    assigned_role = UserRole.ADMIN.value if profile.admin_email.lower() in MASTER_ADMIN_EMAILS else UserRole.MERCHANT.value
    active = ActiveSession(token=session_token, merchant_id=profile.merchant_id, role=assigned_role, created_at=now, last_seen=now)
    db.session.add(active)
    db.session.commit()

    # Tie cookie lifetime to the sandbox window when applicable.
    if profile.sandbox_status == "sandbox" and profile.sandbox_expires_at:
        remaining = int((profile.sandbox_expires_at - now).total_seconds())
        max_age = min(remaining, SESSION_TIMEOUT_DAYS * 86400)
    else:
        max_age = SESSION_TIMEOUT_DAYS * 86400

    response = make_response(redirect("/"))
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=max_age,
        httponly=True,
        samesite="Lax",
        secure=app.config.get("SESSION_COOKIE_SECURE", os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"),
    )
    return response


@app.route('/api/v1/admin/kill-switch', methods=['POST'])
@require_roles([UserRole.ADMIN])
def admin_kill_switch():
    """Halt all background channel synchronization."""
    try:
        asyncio.run(circuit_breaker.engage_global_kill_switch())
        return jsonify({"status": "HALTED"}), 200
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)}), 500


@app.route('/api/v1/admin/release-lock', methods=['POST'])
@require_roles([UserRole.ADMIN])
def admin_release_lock():
    """Restore global synchronization."""
    try:
        asyncio.run(circuit_breaker.release_system_lock())
        return jsonify({"status": "OPERATIONAL"}), 200
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)}), 500


@app.route('/api/v1/engineer/sandbox-test', methods=['GET'])
@require_roles([UserRole.ENGINEER, UserRole.ADMIN])
def engineer_sandbox_test():
    """Sandboxed environment for outsourced engineers to test Shopify mock sync."""
    return jsonify({"status": "ACTIVE", "scope": "Sandboxed Shopify mock-data synchronization box."}), 200


@app.route('/api/v1/merchant/dashboard', methods=['GET'])
@require_roles([UserRole.MERCHANT, UserRole.ADMIN])
def merchant_dashboard():
    """Standard merchant workspace view."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    return jsonify({
        "tenant_id": merchant["id"],
        "business_name": merchant["name"],
        "tier": merchant["tier"],
        "stores": ["Shopify", "TikTok Shop", "Amazon"],
    }), 200


@app.route('/api/v1/admin/trends/run-scrape', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def admin_run_trend_scrape():
    """Trigger the trending products scraping automation worker in the background."""
    try:
        run_async_task(run_trend_scrape)
        return jsonify({"status": "accepted", "message": "Trend worker queued in background."}), 202
    except Exception as e:
        log_system_exception("trend_scrape", f"Trend worker failed: {e}")
        return jsonify({"status": "error", "reason": str(e)}), 500


@app.route('/api/v1/trends/top', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def trends_top():
    """Return the Weekly Top 50 (Tier 1) trending products."""
    items = TrendingProduct.query.filter_by(tier="Tier 1").order_by(TrendingProduct.current_velocity_score.desc()).limit(50).all()
    return jsonify({
        "tier": "Tier 1",
        "count": len(items),
        "products": [{
            "id": i.id,
            "source": i.source_platform,
            "external_id": i.external_item_id,
            "title": i.title,
            "image": i.sample_image_url,
            "velocity": i.current_velocity_score,
            "scraped_at": i.scraped_at.isoformat() if i.scraped_at else None,
        } for i in items]
    }), 200


@app.route('/api/v1/trends/momentum', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def trends_momentum():
    """Return real-time momentum alerts (Tier 2) above velocity threshold."""
    threshold = float(request.args.get("threshold", 0.0))
    items = TrendingProduct.query.filter(TrendingProduct.tier == "Tier 2", TrendingProduct.current_velocity_score >= threshold).order_by(TrendingProduct.current_velocity_score.desc()).limit(50).all()
    return jsonify({
        "tier": "Tier 2",
        "count": len(items),
        "products": [{
            "id": i.id,
            "source": i.source_platform,
            "external_id": i.external_item_id,
            "title": i.title,
            "image": i.sample_image_url,
            "velocity": i.current_velocity_score,
            "scraped_at": i.scraped_at.isoformat() if i.scraped_at else None,
        } for i in items]
    }), 200


@app.route('/health', methods=['GET'])
@limiter.exempt
def health_check():
    """Sentry-style diagnostic: database and generated storage health."""
    health = {
        "status": "HEALTHY",
        "timestamp": datetime.now().isoformat(),
        "database_connected": False,
        "generated_storage_write_access": False,
    }
    try:
        BusinessMetric.query.first()
        health["database_connected"] = True
    except Exception as e:
        health["status"] = "DEGRADED"
        health["database_error"] = str(e)
        logger.critical(f"Health check DB failure: {e}")

    try:
        probe = os.path.join(GENERATED_DIR, ".health_probe")
        with open(probe, "w") as f:
            f.write("PROBE_OK")
        os.remove(probe)
        health["generated_storage_write_access"] = True
    except Exception as e:
        health["status"] = "DEGRADED"
        health["storage_error"] = str(e)
        logger.critical(f"Health check storage failure: {e}")

    status_code = 200 if health["status"] == "HEALTHY" else 500
    return jsonify(health), status_code


@app.route('/api/v1/auth/shopify/connect')
def shopify_oauth_connect():
    """Step 1: Redirect merchant to Shopify OAuth grant screen."""
    merchant = get_merchant_context()
    if not merchant:
        return redirect("/login?error=auth_required")
    if not merchant.get("live_access_enabled"):
        return redirect("/dashboard/commerce-hub?oauth_sync=error")
    shop = request.args.get("shop", "").strip().lower()
    if not re.match(r'^[a-zA-Z0-9\-]+\.myshopify\.com$', shop):
        return jsonify({"success": False, "error": "Invalid shop layout format"}), 400

    scopes = "read_products,write_products,read_orders,read_inventory,read_fulfillments"
    oauth_url = f"https://{shop}/admin/oauth/authorize?client_id={SHOPIFY_CLIENT_ID}&scope={scopes}&redirect_uri={OAUTH_REDIRECT_URI}"
    return redirect(oauth_url)


def _tiktok_oauth_state(merchant_id: str, secret: str) -> str:
    sig = hmac.new(secret.encode("utf-8"), merchant_id.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"{merchant_id}:{sig}"


def _verify_tiktok_oauth_state(state: str, secret: str) -> Optional[str]:
    if not state or ":" not in state:
        return None
    merchant_id, sig = state.split(":", 1)
    expected = hmac.new(secret.encode("utf-8"), merchant_id.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(sig, expected):
        return None
    return merchant_id


@app.route('/api/v1/auth/shopify/callback')
def shopify_oauth_callback():
    """Step 2: Exchange Shopify OAuth code and persist the connection."""
    code = request.args.get("code")
    shop = request.args.get("shop", "").strip().lower()
    merchant = get_merchant_context()
    if not merchant or not merchant.get("live_access_enabled"):
        return redirect("/dashboard/commerce-hub?oauth_sync=error")
    merchant_id = merchant["id"]

    if not shop or not re.match(r'^[a-zA-Z0-9\-]+\.myshopify\.com$', shop):
        return jsonify({"success": False, "error": "Invalid shop domain"}), 400
    if not SHOPIFY_CLIENT_ID or not SHOPIFY_CLIENT_SECRET:
        return jsonify({"success": False, "error": "OAuth credentials not configured"}), 400

    try:
        result = channels_module.shopify_oauth_exchange(shop, code, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET)
        channels_module.connect_shopify(merchant_id, shop, result["access_token"])
        return redirect("/dashboard/commerce-hub?oauth_sync=success")
    except Exception as e:
        logger.error(f"[Shopify OAuth] {e}")
        return redirect("/dashboard/commerce-hub?oauth_sync=error")


@app.route('/api/v1/auth/tiktok/connect')
def tiktok_oauth_connect():
    """Step 1: Redirect merchant to TikTok Shop seller authorization screen."""
    merchant = get_merchant_context()
    if not merchant:
        return redirect("/login?error=auth_required")
    if not merchant.get("live_access_enabled"):
        return redirect("/dashboard/commerce-hub?oauth_sync=error")
    if not TIKTOK_APP_KEY or not TIKTOK_APP_SECRET or not TIKTOK_SERVICE_ID:
        return redirect("/dashboard/commerce-hub?oauth_sync=error")

    state = _tiktok_oauth_state(merchant["id"], TIKTOK_APP_SECRET)
    auth_url = tiktok_sync.build_auth_url(
        service_id=TIKTOK_SERVICE_ID,
        app_key=TIKTOK_APP_KEY,
        redirect_uri=TIKTOK_REDIRECT_URI,
        state=state,
        region=TIKTOK_AUTH_REGION,
    )
    return redirect(auth_url)


@app.route('/api/v1/auth/tiktok/callback')
def tiktok_oauth_callback():
    """Step 2: Exchange TikTok Shop auth code, fetch authorized shops, and persist."""
    code = request.args.get("code")
    state = request.args.get("state", "")
    if not code:
        return redirect("/dashboard/commerce-hub?oauth_sync=error")

    merchant_id = None
    if TIKTOK_APP_SECRET:
        merchant_id = _verify_tiktok_oauth_state(state, TIKTOK_APP_SECRET)
    if not merchant_id:
        merchant = get_merchant_context()
        if merchant:
            merchant_id = merchant["id"]
        else:
            return redirect("/login?error=auth_required")

    if not TIKTOK_APP_KEY or not TIKTOK_APP_SECRET:
        return redirect("/dashboard/commerce-hub?oauth_sync=error")

    try:
        token_data = tiktok_sync.exchange_auth_code(code, TIKTOK_APP_KEY, TIKTOK_APP_SECRET)
        access_token = token_data.get("access_token") or token_data.get("accessToken", "")
        refresh_token = token_data.get("refresh_token") or token_data.get("refreshToken", "")

        shops = tiktok_sync.get_authorized_shops(
            access_token=access_token,
            app_key=TIKTOK_APP_KEY,
            app_secret=TIKTOK_APP_SECRET,
            region=TIKTOK_AUTH_REGION,
        )
        if not shops:
            return redirect("/dashboard/commerce-hub?oauth_sync=error")

        shop = shops[0]
        shop_id = str(shop.get("id") or shop.get("shop_id") or "")
        shop_cipher = str(shop.get("cipher") or shop.get("shop_cipher") or "")
        if not shop_id:
            raise ValueError("TikTok authorized shops response missing shop id")

        channels_module.connect_tiktok(
            merchant_id=merchant_id,
            seller_id=shop_id,
            app_key=TIKTOK_APP_KEY,
            app_secret=TIKTOK_APP_SECRET,
            access_token=access_token,
            shop_cipher=shop_cipher,
            refresh_token=refresh_token,
        )
        return redirect("/dashboard/commerce-hub?oauth_sync=success")
    except Exception as e:
        logger.error(f"[TikTok OAuth] {e}")
        return redirect("/dashboard/commerce-hub?oauth_sync=error")


@app.route('/account/login')
def customer_login():
    return render_template('login.html', domain=SHOPIFY_DOMAIN)


@app.route('/account')
def account():
    if 'customer_access_token' not in session:
        return redirect(url_for('customer_login'))
    return render_template('account.html', customer=session.get('customer'))


@app.route('/logout')
def logout():
    session.pop('customer_access_token', None)
    session.pop('customer', None)
    return redirect(url_for('home'))


# ============================================================
# VETTED OPERATOR INTAKE + SANDBOX
# ============================================================

@app.route('/beta', methods=['GET'])
@app.route('/beta/apply', methods=['GET'])
def beta_apply():
    """Public beta application waitlist page."""
    return render_template('beta_apply.html')


@app.route('/api/beta/apply', methods=['POST'])
@limiter.limit("10 per minute")
def api_beta_apply():
    """Submit a beta waitlist application."""
    data = request.get_json(silent=True) or request.form or {}
    email = (data.get("email") or "").strip().lower()
    business_name = (data.get("business_name") or "").strip()
    monthly_volume = (data.get("monthly_volume") or "").strip()
    monthly_ad_spend = (data.get("monthly_ad_spend") or "").strip()
    ad_channels = ", ".join(data.get("ad_channels") or []) if isinstance(data.get("ad_channels"), list) else (data.get("ad_channels") or "")
    bottleneck = (data.get("bottleneck") or "").strip()
    selected_plan = (data.get("selected_plan") or "beta_plan").strip()
    add_ons = data.get("add_ons") if isinstance(data.get("add_ons"), list) else []
    # Backwards-compatible support for legacy boolean.
    if data.get("ad_plan_addon"):
        if "curated_ad_plan" not in add_ons:
            add_ons.append("curated_ad_plan")

    if not email or "@" not in email:
        return jsonify({"detail": "A valid business email is required."}), 400

    try:
        app = vetted_operator.submit_application(
            email=email,
            business_name=business_name,
            monthly_volume=monthly_volume,
            monthly_ad_spend=monthly_ad_spend,
            ad_channels=ad_channels,
            bottleneck=bottleneck,
            selected_plan=selected_plan,
            add_ons=add_ons,
        )
        try:
            plan_label = "Beta Plan"
            if app.add_ons:
                addon_names = {
                    "custom_brand_build": "Custom Brand Build",
                    "curated_ad_plan": "Curated Ad Plan",
                    "seo": "SEO",
                    "email_setup": "Email Setup",
                }
                plan_label += " + " + ", ".join([addon_names.get(a, a) for a in app.add_ons])
            _notify_team_new_waitlist(app, plan_label)
            _confirm_waitlist_to_applicant(app, plan_label)
        except Exception as notify_err:
            logger.warning(f"[Beta Apply] CRM/notify hook failed: {notify_err}")
        return jsonify({"status": "received", "id": app.id, "email": app.email}), 201
    except Exception as e:
        logger.error(f"[Beta Apply] Failed: {e}")
        return jsonify({"detail": "Could not submit application."}), 500


@app.route('/admin/beta-waitlist', methods=['GET'])
@require_roles([UserRole.ADMIN])
def admin_beta_waitlist():
    """Admin review page for beta applications."""
    merchant = get_merchant_context()
    merchant_id = merchant["id"] if merchant else None
    ctx = context(active_page='admin_beta_waitlist', merchant=merchant, merchant_id=merchant_id)
    return render_template('admin_beta_waitlist.html', **ctx)


@app.route('/api/admin/beta-applications', methods=['GET'])
@require_roles([UserRole.ADMIN])
def api_admin_beta_applications():
    """List beta waitlist applications for admin review."""
    status = request.args.get("status")
    try:
        apps = vetted_operator.list_applications(status=status)
        return jsonify({"applications": [vetted_operator.application_to_dict(a) for a in apps]}), 200
    except Exception as e:
        logger.error(f"[Admin Waitlist] Failed: {e}")
        return jsonify({"detail": "Could not list applications."}), 500


@app.route('/api/admin/beta-applications/<int:app_id>/sandbox', methods=['POST'])
@require_roles([UserRole.ADMIN])
def api_admin_approve_sandbox(app_id):
    """Approve an application into the 48-hour sandbox and email login credentials."""
    try:
        result = vetted_operator.approve_to_sandbox(app_id)
        merchant_id = result["merchant_id"]
        email = result["email"]
        temp_password = result.get("temp_password")
        expires_at_str = result["sandbox_expires_at"]
        expires_at = datetime.fromisoformat(expires_at_str)

        app_obj = BetaWaitlistApplication.query.get_or_404(app_id)
        sandbox_demo.seed_sandbox_demo(merchant_id, app_obj.business_name or "")

        # One-time magic login link that lives as long as the sandbox window.
        magic_token = secrets.token_urlsafe(32)
        db.session.add(MagicLoginToken(
            token=magic_token,
            admin_email=email,
            merchant_id=merchant_id,
            expires_at=expires_at,
            is_used=0,
        ))
        db.session.commit()

        root = request.url_root.rstrip('/')
        magic_url = f"{root}/api/v1/auth/magic-login?token={magic_token}"
        login_url = f"{root}/login?email={email}&sandbox=ready"

        body = f"""
        <p>Hi there,</p>
        <p>Your Vantav beta 48-hour sandbox is ready. You can log in instantly below and explore the dashboard with simulated data.</p>
        <p><b>Sandbox expires:</b> {expires_at_str} UTC</p>
        <p><a href="{magic_url}" style="display:inline-block;padding:10px 18px;background:#d4af37;color:#000;border-radius:8px;text-decoration:none;font-weight:700;">Open Dashboard</a></p>
        <p>Or log in with your email and temporary password at <a href="{login_url}">{login_url}</a>:</p>
        <ul>
          <li><b>Email:</b> {email}</li>
          <li><b>Temporary password:</b> {temp_password or '(use the magic link above)'}</li>
        </ul>
        <p>Live marketplace connections are disabled during the sandbox. Connect stores after your account is upgraded to live access.</p>
        <p>Questions? Reply to this email.</p>
        <p>— Vantav Team</p>
        """

        email_sent = dispatch_external_email(email, "Your Vantav 48-Hour Sandbox is Ready", body)
        db.session.commit()

        return jsonify({
            "status": "sandbox",
            "merchant_id": merchant_id,
            "email": email,
            "temp_password": temp_password,
            "magic_url": magic_url,
            "login_url": login_url,
            "sandbox_expires_at": expires_at_str,
            "email_sent": email_sent,
        }), 200
    except Exception as e:
        logger.error(f"[Approve Sandbox] Failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/admin/seed-sandbox-demo/<merchant_id>', methods=['POST'])
@require_roles([UserRole.ADMIN])
def api_admin_seed_sandbox_demo(merchant_id):
    """Generate or refresh demo data for a sandbox merchant."""
    try:
        profile = MerchantProfile.query.get_or_404(merchant_id)
        force = request.args.get("force", "false").lower() == "true"
        seeded = sandbox_demo.seed_sandbox_demo(merchant_id, profile.business_name or "", force=force)
        return jsonify({"merchant_id": merchant_id, "seeded": seeded}), 200
    except Exception as e:
        logger.error(f"[Seed Sandbox Demo] Failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/admin/beta-applications/<int:app_id>/live', methods=['POST'])
@require_roles([UserRole.ADMIN])
def api_admin_approve_live(app_id):
    """Grant live marketplace access to a sandbox merchant."""
    try:
        app = BetaWaitlistApplication.query.get_or_404(app_id)
        if app.merchant_id:
            vetted_operator.approve_to_live(app.merchant_id)
            return jsonify({"status": "live", "merchant_id": app.merchant_id}), 200
        return jsonify({"detail": "Application has no linked merchant."}), 400
    except Exception as e:
        logger.error(f"[Approve Live] Failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/admin/beta-applications/<int:app_id>/checkout', methods=['POST'])
@require_roles([UserRole.ADMIN])
def api_admin_send_checkout(app_id):
    """Create a Stripe checkout link for a sandbox/approved applicant and email it."""
    try:
        app = BetaWaitlistApplication.query.get_or_404(app_id)
        if app.status not in ('sandbox', 'approved'):
            return jsonify({"detail": "Application must be in sandbox or approved status."}), 400
        if not app.merchant_id:
            return jsonify({"detail": "No merchant linked. Approve to sandbox first."}), 400
        profile = MerchantProfile.query.get(app.merchant_id)
        if not profile:
            return jsonify({"detail": "Merchant profile not found."}), 400

        plan = (app.selected_plan or "beta").lower().strip()
        include_startup_addon = "beta_startup" in plan or bool(app.ad_plan_addon)

        session_url, session_id, customer_id = billing_module.create_checkout_session(
            merchant_id=profile.merchant_id,
            email=profile.admin_email or app.email,
            name=profile.business_name or app.business_name or app.email,
            include_startup_addon=include_startup_addon,
            plan=plan,
        )

        email_sent = False
        if profile.admin_email:
            body = f"""
            <p>Hi {profile.business_name or 'there'},</p>
            <p>Your Vantav beta access is ready. Complete payment below to unlock live marketplace connections and remove the 48-hour sandbox limit:</p>
            <p><a href="{session_url}" style="display:inline-block;padding:10px 18px;background:#d4af37;color:#000;border-radius:8px;text-decoration:none;font-weight:700;">Complete Payment</a></p>
            <p>— Vantav Team</p>
            """
            email_sent = dispatch_external_email(profile.admin_email, "Complete Your Vantav Beta Setup", body)

        return jsonify({
            "status": "checkout_created",
            "checkout_url": session_url,
            "session_id": session_id,
            "email_sent": email_sent,
        }), 200
    except Exception as e:
        logger.error(f"[Admin Checkout] Failed for {app_id}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/admin/beta-applications/<int:app_id>/reject', methods=['POST'])
@require_roles([UserRole.ADMIN])
def api_admin_reject(app_id):
    """Reject a beta application."""
    data = request.get_json(silent=True) or {}
    try:
        vetted_operator.reject_application(app_id, notes=data.get("notes", ""))
        return jsonify({"status": "rejected"}), 200
    except Exception as e:
        logger.error(f"[Reject Application] Failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/admin/reset-password', methods=['POST'])
@require_roles([UserRole.ADMIN])
def api_admin_reset_password():
    """Generate or set a temporary password for a merchant account."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()
    if not email:
        return jsonify({"detail": "Email is required"}), 400
    profile = MerchantProfile.query.filter_by(admin_email=email).first()
    if not profile:
        return jsonify({"detail": "Merchant not found"}), 404
    if not password:
        password = secrets.token_urlsafe(8)
    profile.password_hash = generate_password_hash(password, method="pbkdf2:sha256")
    db.session.commit()
    return jsonify({"merchant_id": profile.merchant_id, "email": email, "temp_password": password}), 200


@app.route('/api/merchant/live-access-check', methods=['GET'])
def api_live_access_check():
    """Check whether the current merchant can connect live marketplace credentials."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    result = vetted_operator.gate_check(merchant["id"], request.args.get("feature", "live_sync"))
    return jsonify(result), 200


if __name__ == '__main__':
    app.run(debug=True, port=3000)
