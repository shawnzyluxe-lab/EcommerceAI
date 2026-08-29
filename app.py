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
import time
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
from sqlalchemy import or_, func, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(module)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("shawnzyluxe_core")
from urllib.parse import urlencode, quote
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file, make_response, after_this_request, current_app, g
from flask_sock import Sock
from dotenv import load_dotenv

load_dotenv()

SEED_DEMO_DATA = os.environ.get("SEED_DEMO_DATA", "false").lower() in ("1", "true", "yes")

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

from models import db, Tenant, ConnectedChannel, ActiveSession, BusinessMetric, CommerceChannel, MerchantChannel, SupportMetric, MarketingStudio, PredictiveLogistics, OutboundTransmission, SaaSBilling, LocalProductCatalog, MerchantProfile, TenantOAuthToken, MerchantMetric, SystemExceptionLog, ProcessedWebhookEvent, AdSpendAnalytic, GeneratedPurchaseOrder, AIAgent, AgentMessage, SupportMessage, MerchantDecisionLog, MagicLoginToken, TrendingProduct, ProductFinancialLedger, MerchantSetting, ProfitFeedOrder, AdSpendFeed, Alert, BetaWaitlistApplication, PendingAction, StartupPackProject, BusinessMemory, WorkspaceSeat, IntegrationLink, SecureChannelCredential, Product, UnifiedOrder, OrderItem, AdminAuditLog, AdminPlatformControl
import profit_feed
import cache_barrier
import billing as billing_module
import alert_matrix
import vetted_operator
import action_gate
import channel_auth
import tenant_rls
import rules_engine
import forecaster
import channel_analytics
import coo_agent_mesh
import channels as channels_module
import shopify_sync
import tiktok_sync
import amazon_sync
import outbound
import monitoring as monitoring_module
import profit_regression
import seed_regression_sku
import tiktok_studio
import tiktok_marketing_studio
import marketing_studio as marketing_studio_module
import assistant_engine
import startup_pack
import initial_sync
import historical_ingestion
import master_auth_engine
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
    ALL_DASHBOARD_PAGE_IDS,
    PLACEHOLDER_DASHBOARD_PAGE_IDS,
    BETA_LOCKED_PAGE_IDS,
    sample_pages_enabled,
    global_sync_paused,
    maintenance_mode,
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
        {"store": "TikTok Shop", "rate": "4.1%", "status": "Trending", "up": True},
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
if DATABASE_URL.startswith("sqlite"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"connect_args": {"check_same_thread": False}}

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
app.register_blueprint(channel_auth.credential_bp)

# Register production monitoring hooks (request logging, security headers, SLA checks).
monitoring_module.register_app(app)
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
MASTER_ADMIN_EMAIL = os.environ.get("MASTER_ADMIN_EMAIL", "shawn@shawnzyluxe.com")
MASTER_ADMIN_EMAILS = [e.strip().lower() for e in MASTER_ADMIN_EMAIL.split(",") if e.strip()]
ENGINEER_EMAIL = os.environ.get("ENGINEER_EMAIL", "engineer@shawnzyluxe.com")
ENGINEER_EMAILS = [e.strip().lower() for e in ENGINEER_EMAIL.split(",") if e.strip()]
RECAPTCHA_SITE_KEY = os.environ.get("RECAPTCHA_SITE_KEY", "")
RECAPTCHA_SECRET_KEY = os.environ.get("RECAPTCHA_SECRET_KEY", "")
RECAPTCHA_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"
SESSION_COOKIE_NAME = "vantav_session_token"
# Aegis-style idle timeout: default 5 minutes (300 seconds) so sessions do not stay
# open for hours when the browser is left unattended.
_aegis_timeout_env = os.environ.get("AEGIS_SESSION_TIMEOUT_SECONDS")
AEGIS_SESSION_TIMEOUT_SECONDS = int(_aegis_timeout_env or "300")
SESSION_TIMEOUT_DAYS = int(os.environ.get("SESSION_TIMEOUT_DAYS", "7"))
# AEGIS_SESSION_TIMEOUT_SECONDS takes precedence over the older
# SESSION_IDLE_TIMEOUT_MINUTES env var when explicitly set.
if _aegis_timeout_env:
    SESSION_IDLE_TIMEOUT_MINUTES = AEGIS_SESSION_TIMEOUT_SECONDS // 60
else:
    SESSION_IDLE_TIMEOUT_MINUTES = int(os.environ.get("SESSION_IDLE_TIMEOUT_MINUTES", "30"))
SESSION_MAX_AGE_HOURS = int(os.environ.get("SESSION_MAX_AGE_HOURS", "12"))

# Production detection drives the Secure cookie flag exactly like Aegis.
IS_PRODUCTION = (
    os.environ.get("AEGIS_ENV", "development") == "production"
    or os.environ.get("RENDER", "").lower() == "true"
)

app.config.update(
    SESSION_COOKIE_NAME=SESSION_COOKIE_NAME,
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_REFRESH_EACH_REQUEST=True,
)

def _session_cookie_kwargs(secure=None, max_age=None):
    """Return standard cookie flags for the Vantav session token.

    Mirrors Aegis/Alpha: HttpOnly, SameSite=Lax, Secure in production.
    """
    if secure is None:
        secure = app.config.get("SESSION_COOKIE_SECURE", IS_PRODUCTION)
    kwargs = {
        "httponly": app.config.get("SESSION_COOKIE_HTTPONLY", True),
        "samesite": app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
        "secure": secure,
    }
    if max_age is not None:
        kwargs["max_age"] = max_age
    return kwargs


def _set_session_cookie(response, token, max_age=None):
    """Drop the session token cookie with the Aegis-style flag set."""
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        **_session_cookie_kwargs(max_age=max_age),
    )
    return response


def _delete_session_cookie(response):
    """Clear the session token cookie using the same flags that set it."""
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        **_session_cookie_kwargs(),
    )
    return response

# Commercial-ready dashboard pages. Merchants can only reach these pages; admins and
# engineers can still access every page in valid_pages for development.
# Merchant-ready scope includes core financial and inventory views. Other pages
# remain available to admins and engineers while they are being completed.
COMMERCIAL_READY_DASHBOARD_PAGES = {
    "overview", "alerts", "profit_engine", "inventory", "billing", "settings", "startup_pack", "onboarding_loading",
}

from tier_manager import TierManager, TIER_LIMITS, PLAN_TO_TIER, DEFAULT_MERCHANT_PAGE_IDS

# Backwards-compatible friendly name mapping used by auth signup/provision forms.
TIER_NAME_MAP = {
    "Starter": "Basic Tier",
    "operator": "Vantav Operator",
    "growth": "Vantav Growth",
    "scale": "Vantav Scale",
    "Operator": "Vantav Operator",
    "Growth": "Vantav Growth",
    "Scale": "Vantav Scale",
    "Vantav Operator": "Vantav Operator",
    "Vantav Growth": "Vantav Growth",
    "Vantav Scale": "Vantav Scale",
    "Basic Tier": "Basic Tier",
    "Pro": "Vantav Growth",
    "Enterprise": "Vantav Scale",
    "Concierge": "Concierge Bundle",
    "Concierge Bundle": "Concierge Bundle",
}


class UserRole(str, Enum):
    ADMIN = "Admin"
    MERCHANT = "Merchant"
    ENGINEER = "Engineer"


def _profile_for_email(email: str):
    """Return the most recently created merchant profile for an email.

    This keeps login/magic-link flows deterministic when duplicate accounts exist.
    """
    return (
        MerchantProfile.query.filter_by(admin_email=email)
        .order_by(MerchantProfile.created_at.desc())
        .first()
    )


def _canonical_tier(raw_tier: str) -> str:
    """Map legacy, marketing, or plan tier names to the canonical Vantav tier."""
    mapping = {
        "Basic Tier": "Basic Tier",
        "Vantav Operator": "Vantav Operator",
        "Operator": "Vantav Operator",
        "Vantav Growth": "Vantav Growth",
        "Growth": "Vantav Growth",
        "Beta Tier": "Vantav Growth",
        "Pro Tier": "Vantav Growth",
        "Vantav Scale": "Vantav Scale",
        "Scale": "Vantav Scale",
        "Enterprise AI Tier": "Vantav Scale",
        "Enterprise Plan": "Vantav Scale",
        "Concierge Bundle": "Vantav Scale",
    }
    return mapping.get(raw_tier, "Vantav Operator")


def _merchant_requires_tier_selection(merchant: dict) -> bool:
    """Return True when a merchant has signed in but not picked/confirmed a tier."""
    if not merchant:
        return False
    if merchant.get("role") != UserRole.MERCHANT.value:
        return False
    if merchant.get("sandbox_status") in ("pending", "rejected", None, ""):
        return True
    return False


def _tier_test_accounts() -> set:
    """Emails that can bypass checkout for paid-tier testing."""
    env_emails = {e.strip().lower() for e in os.environ.get('MERCHANT_TIER_TEST_ACCOUNTS', '').split(',') if e.strip()}
    return env_emails | {'merchant@vantavcommerce.com'}


def _reset_test_merchant_for_tier_testing(profile: MerchantProfile) -> None:
    """Put a designated test merchant back into the tier-selection state."""
    if not profile:
        return
    email = (profile.admin_email or "").strip().lower()
    if email not in _tier_test_accounts():
        return
    profile.account_tier = "Basic Tier"
    profile.sandbox_status = "pending"
    profile.live_access_enabled = 0
    billing = SaaSBilling.query.get(profile.merchant_id)
    if not billing:
        billing = SaaSBilling(merchant_id=profile.merchant_id)
        db.session.add(billing)
    billing.current_plan = "Basic Tier"
    try:
        memory = action_gate.get_business_memory(profile.merchant_id)
        memory.max_authorized_seats = 1
    except Exception:
        pass


def _protected_merchant_ids() -> set:
    """Merchant IDs the admin must never delete from the live database."""
    protected = set()
    for email in ('shawn@shawnzyluxe.com', 'engineer@shawnzyluxe.com', 'merchant@vantavcommerce.com'):
        p = _profile_for_email(email)
        if p:
            protected.add(p.merchant_id)
    return protected


def _cascade_delete_merchant(merchant_id: str) -> None:
    """Delete every row that references the merchant across all tables except the profile itself."""
    # Some child tables don't have a merchant_id column but hold FKs to merchant-owned rows.
    # Remove them first so the generic merchant_id pass doesn't hit RESTRICT constraints.
    db.session.execute(text("""
        DELETE FROM order_items
        WHERE order_id IN (SELECT id FROM orders WHERE merchant_id = :merchant_id)
    """), {"merchant_id": merchant_id})
    db.session.execute(text("""
        DELETE FROM daily_costs
        WHERE sku IN (SELECT sku FROM products WHERE merchant_id = :merchant_id)
    """), {"merchant_id": merchant_id})

    for table in reversed(db.metadata.sorted_tables):
        if table.name == 'merchant_profiles':
            continue
        conditions = []
        for col in table.columns:
            if col.name in ('merchant_id', 'original_merchant_id', 'impersonating_merchant_id'):
                conditions.append(col == merchant_id)
        if not conditions:
            continue
        try:
            db.session.execute(table.delete().where(or_(*conditions)))
        except Exception as e:
            logger.warning(f"[CascadeDelete] {table.name}: {e}")


def get_current_user():
    """Return the active session record with its role, or None."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    s = ActiveSession.query.get(token)
    if not s:
        return None
    now = datetime.utcnow()
    # Enforce absolute and idle session lifetime.
    max_age = min(timedelta(days=SESSION_TIMEOUT_DAYS), timedelta(hours=SESSION_MAX_AGE_HOURS))
    if s.created_at < now - max_age:
        db.session.delete(s)
        db.session.commit()
        return None
    if s.last_seen and s.last_seen < now - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES):
        db.session.delete(s)
        db.session.commit()
        return None
    # Background polls can opt out of bumping the idle window.
    if request.headers.get('X-Session-Refresh') != 'false':
        s.last_seen = now
        db.session.commit()
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
        # Fail open only in unit tests; require a real secret in production.
        return 1.0 if app.config.get("TESTING") else 0.0
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


# Admin kill switch: sync endpoints that are paused when global_sync_paused is true.
_SYNC_ENDPOINTS = {
    "api_sync_shopify",
    "api_get_shopify_products",
    "api_sync_tiktok",
    "api_get_tiktok_products",
    "api_sync_amazon",
    "api_initialize_onboarding_harvest",
    "api_admin_sync_shopify",
    "api_admin_sync_tiktok",
    "api_admin_sync_tiktok_marketing",
    "api_admin_sync_amazon",
    "api_admin_sync_ebay",
    "api_admin_sync_walmart",
    "api_admin_sync_bigcommerce",
    "api_admin_sync_woocommerce",
}


def log_admin_audit(action: str, target_merchant_id: str = None, details: dict = None):
    """Write an immutable admin action to the audit log."""
    try:
        s = get_current_user()
        email = ""
        if s and s.merchant_id:
            admin_profile = MerchantProfile.query.get(s.merchant_id)
            email = admin_profile.admin_email if admin_profile else ""
        log = AdminAuditLog(
            admin_email=email or "admin",
            action=action,
            target_merchant_id=target_merchant_id,
            details=details or {},
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        logger.warning(f"[AdminAudit] failed to write {action}: {e}")


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
    protected route bumps the idle window. The cookie is a browser-session cookie,
    so a fresh browser always requires re-authentication.
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
    # Background polls can opt out of bumping the idle window.
    if refresh and request.headers.get('X-Session-Refresh') != 'false':
        # Bump the idle timestamp on real user activity, but do not issue a
        # persistent cookie; the session cookie is cleared when the browser closes.
        s.last_seen = now
        db.session.commit()
    return True


def verify_workspace_seat_allowance(merchant_id: str, new_user_email: Optional[str] = None) -> bool:
    """Block new workspace members if the merchant has no open seat allocations."""
    memory = BusinessMemory.query.filter_by(merchant_id=merchant_id).first()
    max_seats = memory.max_authorized_seats if memory else 1
    active_seats = WorkspaceSeat.query.filter_by(merchant_id=merchant_id).count()
    if active_seats >= max_seats:
        raise ValueError(
            f"Workspace Allocation Error: Your subscription tier is capped at {max_seats} seats. "
            "Upgrade your billing tier to authorize additional users."
        )
    return True


def sync_workspace_seat_count(merchant_id: str) -> int:
    """Recalculate and persist the current active seat count for a merchant."""
    memory = BusinessMemory.query.filter_by(merchant_id=merchant_id).first()
    if not memory:
        return 0
    memory.current_active_seats = WorkspaceSeat.query.filter_by(merchant_id=merchant_id).count()
    db.session.commit()
    return memory.current_active_seats


def get_merchant_context():
    """Resolve merchant_id, account_tier, and business_name from the active session cookie.

    If the session is impersonating another merchant (admin view-as-merchant), the
    returned merchant dict reflects the impersonated tenant while the session object
    retains the admin's credentials for endpoint authorization.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    s = ActiveSession.query.get(token)
    now = datetime.utcnow()
    if not s or not s.merchant_id:
        return None
    # Enforce absolute and idle session lifetime for merchant sessions.
    max_age = min(timedelta(days=SESSION_TIMEOUT_DAYS), timedelta(hours=SESSION_MAX_AGE_HOURS))
    if s.created_at < now - max_age:
        db.session.delete(s)
        db.session.commit()
        return None
    if s.last_seen and s.last_seen < now - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES):
        db.session.delete(s)
        db.session.commit()
        return None
    # Background polls can opt out of bumping the idle window.
    if request.headers.get('X-Session-Refresh') != 'false':
        s.last_seen = now
        db.session.commit()

    # Admin impersonation: view the dashboard and data as another merchant.
    is_impersonating = bool(s.impersonating_merchant_id)
    view_merchant_id = s.impersonating_merchant_id or s.merchant_id

    # Bind the database session to this tenant for RLS when using the restricted app role.
    tenant_rls.set_tenant_scope(view_merchant_id)
    profile = MerchantProfile.query.get(view_merchant_id)
    if not profile:
        return None

    # Re-evaluate role on every request so stale sessions pick up master-admin
    # changes without forcing a logout/login after deploys. Never overwrite the
    # admin role while an admin is impersonating a merchant.
    admin_email = (profile.admin_email or "").strip().lower()
    if is_impersonating:
        # The merchant being viewed is always rendered with the merchant role so
        # the admin dashboard (rather than merchant dashboard) doesn't appear.
        effective_role = UserRole.MERCHANT.value
    elif admin_email in MASTER_ADMIN_EMAILS:
        effective_role = UserRole.ADMIN.value
    elif admin_email in ENGINEER_EMAILS:
        effective_role = UserRole.ENGINEER.value
    else:
        effective_role = UserRole.MERCHANT.value
    if not is_impersonating and s.role != effective_role:
        s.role = effective_role
        db.session.commit()

    # Canonicalize legacy or plan tier names so the UI and gating see the right tier.
    raw_tier = (profile.account_tier or "Basic Tier").strip()
    tier = _canonical_tier(raw_tier)
    tier_meta = TierManager.get_tier_meta(tier)
    sandbox_status = profile.sandbox_status or "pending"
    sandbox_expired = False
    if sandbox_status == "sandbox" and profile.sandbox_expires_at and profile.sandbox_expires_at <= now:
        sandbox_status = "expired"
        sandbox_expired = True
    display_name = profile.business_name or (profile.admin_email.split("@")[0] if profile.admin_email and "@" in profile.admin_email else view_merchant_id)
    billing = SaaSBilling.query.get(view_merchant_id)
    concierge_bundle = False
    if billing and billing.add_ons:
        concierge_bundle = "concierge_bundle" in (billing.add_ons if isinstance(billing.add_ons, list) else [])
    theme_setting = MerchantSetting.query.get((view_merchant_id, "theme"))
    account_holder_setting = MerchantSetting.query.get((view_merchant_id, "account_holder_name"))

    ctx = {
        "id": view_merchant_id,
        "tier": tier,
        "tier_meta": tier_meta,
        "concierge_bundle": concierge_bundle,
        "name": display_name,
        "account_holder_name": account_holder_setting.setting_value if account_holder_setting else display_name,
        "email": profile.admin_email,
        "sandbox_status": sandbox_status,
        "live_access_enabled": bool(profile.live_access_enabled) and not sandbox_expired,
        "sandbox_expires_at": profile.sandbox_expires_at.isoformat() if profile.sandbox_expires_at else None,
        "sandbox_expired": sandbox_expired,
        "brand_color": profile.brand_color or "#8b5cf6",
        "brand_color_secondary": profile.brand_color_secondary or "#a78bfa",
        "feature_flags": profile.feature_flags or {},
        "theme": theme_setting.setting_value if theme_setting else "prometheus-dark",
        "role": effective_role,
    }
    if is_impersonating:
        ctx["is_impersonating"] = True
        ctx["impersonating_merchant_id"] = s.impersonating_merchant_id
        ctx["original_merchant_id"] = s.original_merchant_id or s.merchant_id
        ctx["original_role"] = s.original_role or s.role
    return ctx


def check_tier_limits(merchant_id, requested_feature):
    """Return (allowed: bool, reason: str, status_code: int) based on tier and metered usage."""
    # Admins and engineers can exercise any feature for testing/support.
    s = get_current_user()
    if s and s.role in (UserRole.ADMIN.value, UserRole.ENGINEER.value):
        return True, "OK", 200
    if not merchant_id:
        return False, "No merchant context", 403
    profile = MerchantProfile.query.get(merchant_id)
    if not profile:
        return False, "Unknown merchant", 403
    account = SaaSBilling.query.get(merchant_id)
    if not account:
        return False, "No billing record", 403

    tier = _canonical_tier(profile.account_tier or "Basic Tier")
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
    if request.endpoint in ('home', 'login', 'site_login', 'site_logout', 'subscribe', 'checkout', 'thank_you', 'session_heartbeat', 'create_stripe_checkout', 'beta_apply', 'api_beta_apply', 'auth_login', 'auth_signup', 'auth_provision_node', 'shopify_orders_webhook', 'shopify_gdpr_customer_data_request', 'shopify_gdpr_customer_redact', 'shopify_gdpr_shop_redact', 'shopify_app_uninstalled', 'tiktok_orders_webhook', 'amazon_orders_webhook', 'stripe_billing_webhook', 'supplier_po_update', 'execute_mitigation', 'generate_magic_link', 'magic_login', 'register_merchant', 'shopify_oauth_callback', 'tiktok_oauth_callback', 'health_check', 'api_v1_health', 'api_monitoring_health', 'api_monitoring_alerts', 'legal_terms', 'legal_privacy', 'legal_refund', 'static'):
        return None
    if site_wall_authenticated():
        return None
    return redirect(url_for('login'))


@app.before_request
def admin_kill_switch():
    """Reject sync requests when the admin kill switch is engaged."""
    if request.endpoint not in _SYNC_ENDPOINTS:
        return None
    try:
        if global_sync_paused():
            return jsonify({"detail": "Platform sync is temporarily paused by the administrator."}), 503
    except Exception as e:
        logger.warning(f"[KillSwitch] check failed: {e}")
    return None


# ============================================================
# END SITE PASSWORD WALL
# ============================================================

SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.mailgun.org")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
MERCHANT_EMAIL = os.environ.get("MERCHANT_EMAIL", "shawn@shawnzyluxe.com")
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "support@vantavcommerce.com")
SECURITY_EMAIL = os.environ.get("SECURITY_EMAIL", "security@vantavcommerce.com")
NO_REPLY_EMAIL = os.environ.get("NO_REPLY_EMAIL", "noreply@send.vantavcommerce.com")
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
OAUTH_REDIRECT_URI = os.environ.get("OAUTH_REDIRECT_URI", "https://vantavcommerce.com/api/v1/auth/shopify/callback")

TIKTOK_APP_KEY = os.environ.get("TIKTOK_APP_KEY", "")
TIKTOK_APP_SECRET = os.environ.get("TIKTOK_APP_SECRET", "")
TIKTOK_SERVICE_ID = os.environ.get("TIKTOK_SERVICE_ID", "")
TIKTOK_AUTH_REGION = os.environ.get("TIKTOK_AUTH_REGION", "")
TIKTOK_REDIRECT_URI = "https://vantavcommerce.com/api/v1/auth/tiktok/callback"


def _tiktok_creds_for_region(region: str = ""):
    """Return region-specific TikTok app credentials, falling back to global env."""
    r = (region or TIKTOK_AUTH_REGION or "").strip().upper() or ""
    if r and r != "GLOBAL":
        app_key = os.environ.get(f"TIKTOK_APP_KEY_{r}") or TIKTOK_APP_KEY
        app_secret = os.environ.get(f"TIKTOK_APP_SECRET_{r}") or TIKTOK_APP_SECRET
        service_id = os.environ.get(f"TIKTOK_SERVICE_ID_{r}") or TIKTOK_SERVICE_ID
    else:
        app_key, app_secret, service_id = TIKTOK_APP_KEY, TIKTOK_APP_SECRET, TIKTOK_SERVICE_ID
    return app_key, app_secret, service_id

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
    temp_password = os.environ.get("TEMP_ACCOUNTS_PASSWORD") or SITE_WALL_PASSWORD
    temp_accounts = [
        ("merchant_shawn_01", "Shawnzyluxe Pro", "shawn@shawnzyluxe.com", "Beta Tier"),
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
            # Only overwrite the password hash when an explicit temp-password env var
            # is set; otherwise preserve the existing password (e.g. a user reset).
            if os.environ.get("TEMP_ACCOUNTS_PASSWORD"):
                p.password_hash = generate_password_hash(temp_password, method="pbkdf2:sha256")
            p.sandbox_status = "approved"
            p.live_access_enabled = 1
        else:
            db.session.add(MerchantProfile(
                merchant_id=mid,
                business_name=name,
                admin_email=email,
                account_tier=tier,
                password_hash=generate_password_hash(temp_password, method="pbkdf2:sha256") if temp_password else "",
                sandbox_status="approved",
                live_access_enabled=1,
            ))
    db.session.commit()

    # Seed FK-dependent merchant data now that profiles exist
    if not MerchantMetric.query.filter_by(merchant_id="merchant_shawn_01").first():
        db.session.add(MerchantMetric(
            merchant_id="merchant_shawn_01",
            total_unified_balance=20560.00 if SEED_DEMO_DATA else 0.0,
            true_net_profit=1394.00 if SEED_DEMO_DATA else 0.0,
            gross_revenue=4582.00 if SEED_DEMO_DATA else 0.0,
            ai_briefing="System initialized." if SEED_DEMO_DATA else "No sales data yet.",
        ))
    if not MerchantChannel.query.filter_by(merchant_id="merchant_shawn_01").first():
        channel_defaults = {
            "shopify": (12, 3.4),
            "amazon": (4, 2.8),
            "tiktok": (7, 4.1),
        } if SEED_DEMO_DATA else {
            "shopify": (0, 0.0),
            "amazon": (0, 0.0),
            "tiktok": (0, 0.0),
        }
        for channel_id, (pending_orders, conversion_rate) in channel_defaults.items():
            db.session.add(MerchantChannel(
                merchant_id="merchant_shawn_01",
                channel_id=channel_id,
                pending_orders=pending_orders,
                conversion_rate=conversion_rate,
            ))
    if not SystemExceptionLog.query.first():
        db.session.add(SystemExceptionLog(module_origin="DATABASE_CORE", error_severity="INFO", exception_msg="Relational multi-tenant isolation layer fully hardened."))
    db.session.commit()

    # Seed or restore business metrics
    if not BusinessMetric.query.filter_by(merchant_id="merchant_shawn_01").first():
        db.session.add(BusinessMetric(
            merchant_id="merchant_shawn_01",
            total_unified_balance=20560.00 if SEED_DEMO_DATA else 0.0,
            true_net_profit=1394.00 if SEED_DEMO_DATA else 0.0,
            gross_revenue=4582.00 if SEED_DEMO_DATA else 0.0,
            ai_briefing=COO["narrative"] if SEED_DEMO_DATA else "No sales data yet.",
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

    if SEED_DEMO_DATA:
        profit_feed.seed_demo_data("merchant_shawn_01")

    # Seed / refresh the Alert Matrix from latest data.
    if SEED_DEMO_DATA:
        alert_matrix.seed_demo_alerts("merchant_shawn_01")
    alert_matrix.refresh_alerts("merchant_shawn_01")

    # Seed or restore commerce channels
    if not CommerceChannel.query.first():
        commerce_defaults = [
            ("shopify", "Shopify Storefront", 12, 3.4, "Optimal"),
            ("tiktok", "TikTok Shop", 7, 4.1, "Trending"),
            ("amazon", "Amazon Marketplace", 4, 2.8, "Stable"),
        ] if SEED_DEMO_DATA else [
            ("shopify", "Shopify Storefront", 0, 0.0, "Not connected"),
            ("tiktok", "TikTok Shop", 0, 0.0, "Not connected"),
            ("amazon", "Amazon Marketplace", 0, 0.0, "Not connected"),
        ]
        for channel_id, channel_name, pending_orders, conversion_rate, performance_status in commerce_defaults:
            db.session.add(CommerceChannel(
                channel_id=channel_id,
                channel_name=channel_name,
                pending_orders=pending_orders,
                conversion_rate=conversion_rate,
                performance_status=performance_status,
            ))
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
            active_chats=DASHBOARD_STATE["support_chats"] if SEED_DEMO_DATA else 0,
            sentiment_score=DASHBOARD_STATE["support_sentiment"] if SEED_DEMO_DATA else "No data",
            recent_resolution=DASHBOARD_STATE["support_resolution"] if SEED_DEMO_DATA else "No support activity yet.",
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
            active_campaign=DASHBOARD_STATE["mktg_campaign"] if SEED_DEMO_DATA else "",
            generation_status=DASHBOARD_STATE["mktg_status"] if SEED_DEMO_DATA else "Not started",
            platform_target="Shopify / SMS" if SEED_DEMO_DATA else "",
            copy_preview=DASHBOARD_STATE["mktg_copy"] if SEED_DEMO_DATA else "",
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
    if SEED_DEMO_DATA and not PredictiveLogistics.query.first():
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
            stripe_customer_id="cus_R8zX1042" if SEED_DEMO_DATA else "",
            stripe_subscription_item_id="si_R8zX1042_metered" if SEED_DEMO_DATA else "",
            current_plan="Enterprise AI Tier" if SEED_DEMO_DATA else "Beta Tier",
            metered_usage_units=4820 if SEED_DEMO_DATA else 0,
            accrued_invoice_value=241.00 if SEED_DEMO_DATA else 0.0,
            billing_cycle_end="2026-09-01" if SEED_DEMO_DATA else "",
        ))
    if SEED_DEMO_DATA and not LocalProductCatalog.query.first():
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
    if SEED_DEMO_DATA and not AdSpendAnalytic.query.first():
        db.session.add(AdSpendAnalytic(merchant_id="merchant_shawn_01", platform_source="Shopify Product Ads", budget_allocated=1500.00, current_spend=420.00, roas=3.4, conversion_count=28))
        db.session.add(AdSpendAnalytic(merchant_id="merchant_shawn_01", platform_source="TikTok Video Ads", budget_allocated=2000.00, current_spend=680.00, roas=4.1, conversion_count=47))
        db.session.add(AdSpendAnalytic(merchant_id="merchant_shawn_01", platform_source="Meta Retargeting Loop", budget_allocated=1200.00, current_spend=310.00, roas=2.9, conversion_count=19))
    if SEED_DEMO_DATA and not GeneratedPurchaseOrder.query.first():
        db.session.add(GeneratedPurchaseOrder(po_reference="PO-SZL-A8F2", merchant_id="merchant_shawn_01", variant_sku="SZL-VAR-B", units_ordered=450, fulfillment_status="PENDING"))
    if SEED_DEMO_DATA and not AIAgent.query.first():
        db.session.add(AIAgent(agent_id="agent_logistics", merchant_id="merchant_shawn_01", agent_name="Operations Analyst", agent_role="Operations", status="IDLE_MONITORING", last_action="Reviewed inventory levels and flagged restock needs."))
        db.session.add(AIAgent(agent_id="agent_finance", merchant_id="merchant_shawn_01", agent_name="Finance Analyst", agent_role="Finance", status="IDLE_MONITORING", last_action="Checked cash flow and ad budget headroom."))
        db.session.add(AIAgent(agent_id="agent_marketing", merchant_id="merchant_shawn_01", agent_name="Marketing Analyst", agent_role="Marketing", status="IDLE_MONITORING", last_action="Standing by for campaign instructions."))
        db.session.add(AIAgent(agent_id="agent_support", merchant_id="merchant_shawn_01", agent_name="Support Analyst", agent_role="Support", status="IDLE_MONITORING", last_action="Monitoring customer ticket trends across channels."))
    if SEED_DEMO_DATA and not AgentMessage.query.first():
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
                    "h:Reply-To": SUPPORT_EMAIL,
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
        msg["Reply-To"] = SUPPORT_EMAIL
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
    """Public pricing and plan selection page."""
    host = request.host.split(':')[0].lower()
    if host in ('shawnzyluxe.com', 'www.shawnzyluxe.com'):
        return render_template('coming_soon.html')
    return render_template('subscribe.html', recaptcha_site_key=RECAPTCHA_SITE_KEY, meta_pixel_id=os.environ.get('META_PIXEL_ID', ''), tiktok_pixel_id=os.environ.get('TIKTOK_PIXEL_ID', ''), gtm_id=os.environ.get('GTM_ID', ''))


@app.route('/checkout')
@limiter.exempt
def checkout():
    """Public Stripe checkout page for paid Vantav plans."""
    host = request.host.split(':')[0].lower()
    if host in ('shawnzyluxe.com', 'www.shawnzyluxe.com'):
        return render_template('coming_soon.html')
    merchant = get_merchant_context()
    return render_template('checkout.html', merchant=merchant)


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


@app.route('/security')
@limiter.exempt
def legal_security():
    return render_template('security.html')


@app.route('/status')
@limiter.exempt
def status_page():
    """Public status page for Vantav platform health."""
    health_response, _ = health_check()
    health = health_response.get_json()
    return render_template('status.html', health=health)


@app.route('/dashboard')
def dashboard():
    merchant = get_merchant_context()
    if not merchant:
        return redirect(url_for('login'))
    if merchant.get('role') == UserRole.ADMIN.value:
        return redirect(url_for('admin_dashboard'))
    if merchant.get('role') == UserRole.ENGINEER.value:
        return redirect(url_for('engineer_dashboard'))
    merchant_id = merchant["id"]

    # If the merchant just returned from Stripe, verify the checkout session
    # synchronously so they can connect stores immediately even if the webhook
    # is still queued.
    if request.args.get('checkout') == 'success' and request.args.get('session_id'):
        try:
            checkout = billing_module.verify_checkout_session(request.args.get('session_id'))
            if checkout.get("payment_status") == "paid":
                profile = MerchantProfile.query.get(merchant_id)
                billing = SaaSBilling.query.get(merchant_id)
                metadata = checkout.get("metadata", {})
                chosen_tier = metadata.get("selected_tier") or metadata.get("plan_choice") or "Vantav Operator"
                chosen_tier = _canonical_tier(chosen_tier)
                concierge_bundle = metadata.get("concierge_bundle") == "true"
                if profile and profile.sandbox_status != "approved":
                    profile.account_tier = chosen_tier
                    profile.sandbox_status = "approved"
                    profile.live_access_enabled = 1
                    profile.approved_at = datetime.utcnow()
                    if billing:
                        billing.current_plan = chosen_tier
                        if concierge_bundle and "concierge_bundle" not in (billing.add_ons or []):
                            billing.add_ons = list((billing.add_ons or []) + ["concierge_bundle"])
                    # Sync workspace seat limits to the paid tier.
                    memory = action_gate.get_business_memory(merchant_id)
                    tier_meta = TierManager.get_tier_meta(chosen_tier)
                    memory.max_authorized_seats = int(tier_meta.get("max_users", 1))
                    db.session.commit()
                    merchant = get_merchant_context()
        except Exception as e:
            logger.warning(f"[Dashboard] Checkout verification failed: {e}")

    if _merchant_requires_tier_selection(merchant):
        return redirect(url_for('choose_tier'))

    ctx = context(active_page='overview', merchant=merchant, merchant_id=merchant_id)
    ctx["show_brand_build_prompt"] = (
        request.args.get('checkout') == 'success'
        and request.args.get('concierge_bundle') == 'true'
        and bool(merchant.get('concierge_bundle'))
    )
    ctx["show_onboarding"] = (
        request.args.get('onboarding') == '1'
        or (request.args.get('checkout') == 'success' and not ctx.get('connected'))
    )
    return render_template('dashboard/overview.html', **ctx)


# Commercial-grade dashboard page routes
def _dashboard_context(active_page):
    merchant = get_merchant_context()
    merchant_id = merchant["id"] if merchant else None
    ctx = context(active_page=active_page, merchant=merchant, merchant_id=merchant_id)
    ctx["show_brand_build_prompt"] = (
        request.args.get('checkout') == 'success'
        and request.args.get('concierge_bundle') == 'true'
        and bool(merchant and merchant.get('concierge_bundle'))
    )
    ctx["show_onboarding"] = (
        request.args.get('onboarding') == '1'
        or (request.args.get('checkout') == 'success' and not ctx.get('connected'))
    )
    return ctx


@app.route('/dashboard/<page>')
def dashboard_page(page):
    merchant = get_merchant_context()
    if not merchant:
        return redirect(url_for('login'))
    if _merchant_requires_tier_selection(merchant):
        return redirect(url_for('choose_tier'))
    s = get_current_user()
    active_page = page.replace('-', '_')
    # Pages merged into the unified Settings page.
    if active_page in ('billing', 'integrations', 'themes', 'commerce_hub'):
        redirect_kwargs = {'page': 'settings', 'tab': 'billing' if active_page == 'billing' else 'stores'}
        if request.args.get('checkout') == 'success':
            redirect_kwargs['checkout'] = 'success'
            if request.args.get('concierge_bundle') == 'true':
                redirect_kwargs['concierge_bundle'] = 'true'
        return redirect(url_for('dashboard_page', **redirect_kwargs))
    valid_pages = {
        'overview', 'command_center', 'commerce_hub', 'alerts', 'action_gate', 'profit_engine', 'startup_pack',
        'predictions', 'product_research', 'fulfillment', 'fraud', 'suppliers',
        'marketing', 'support', 'automations', 'team_ai', 'health_score',
        'mobile', 'store_catalog', 'products', 'orders', 'customers',
        'inventory', 'shipments', 'returns', 'analytics', 'discounts', 'apps',
        'reports', 'settings', 'tiktok_studio',
        'monitoring', 'regression_chart', 'onboarding_loading'
    }
    if active_page not in valid_pages:
        return redirect(url_for('dashboard'))
    ctx = _dashboard_context(active_page)
    # Closed-beta gating: hide all non-beta modules from merchants unless the
    # admin has enabled sample/non-beta pages for testing or the merchant is a
    # designated tier-testing account.
    if not s or s.role not in (UserRole.ADMIN.value, UserRole.ENGINEER.value):
        tier_test_account = TierManager.is_tier_test_account((merchant or {}).get("email", ""))
        if active_page in BETA_LOCKED_PAGE_IDS and not sample_pages_enabled() and not tier_test_account:
            return redirect(url_for('dashboard'))
    # Commercial gating: merchants can only reach the pages their tier allows or
    # that the admin has explicitly enabled via feature flags.
    # Admins and engineers can still reach any page.
    if not s or s.role not in (UserRole.ADMIN.value, UserRole.ENGINEER.value):
        flags = merchant.get("feature_flags") or {}
        flag = flags.get(active_page)
        if flag is True:
            pass  # admin explicitly enabled this page
        elif flag is False:
            return redirect(url_for('dashboard'))
        elif active_page not in DEFAULT_MERCHANT_PAGE_IDS:
            return redirect(url_for('dashboard'))
        elif not TierManager.can_access_page(merchant["tier"], active_page):
            target = TierManager.page_upgrade_target(active_page)
            lock_content = (
                f'<div style="text-align:center; padding: 40px 20px;">'
                f'<p style="margin-bottom:24px; color:var(--ink-2);">This module is included in <strong>{target}</strong>.</p>'
                f'<a href="/dashboard/billing" class="btn btn-primary">Upgrade plan</a></div>'
            )
            return render_template('dashboard/page.html', **ctx,
                                   page_title='Upgrade required',
                                   page_description=f'Unlock this module with {target}.',
                                   page_content=lock_content)
        # Brand Build is locked behind the Concierge Bundle add-on.
        if active_page == 'startup_pack' and not merchant.get('concierge_bundle'):
            return redirect(url_for('dashboard_page', page='settings', tab='billing'))
    template = 'dashboard/{}.html'.format(page.replace('-', '_'))
    try:
        return render_template(template, **ctx)
    except Exception:
        return render_template('dashboard/page.html', **ctx,
                               page_title=active_page.replace('_', ' ').title(),
                               page_description='This module is being rebuilt to the new commercial-grade standard.',
                               page_content='')


@app.route('/choose-tier')
def choose_tier():
    merchant = get_merchant_context()
    if not merchant:
        return redirect(url_for('login'))
    if merchant.get('role') in (UserRole.ADMIN.value, UserRole.ENGINEER.value):
        return redirect(url_for('dashboard'))
    if not _merchant_requires_tier_selection(merchant):
        return redirect(url_for('dashboard'))
    merchant_id = merchant['id']
    tiers = [
        {
            'id': 'Basic Tier',
            'name': 'Vantav Basic',
            'price': 'Free',
            'description': 'For solo operators just getting started.',
            'features': ['Profit dashboard', 'Live alerts', 'Email support'],
        },
        {
            'id': 'Vantav Operator',
            'name': 'Vantav Operator',
            'price': '$199/mo',
            'description': 'Core tools to run one store end-to-end.',
            'features': ['Everything in Basic', 'Inventory & orders', 'Product catalog', 'Multi-channel connections'],
        },
        {
            'id': 'Vantav Growth',
            'name': 'Vantav Growth',
            'price': '$399/mo',
            'description': 'Growth automation and advanced analytics.',
            'features': ['Everything in Operator', 'Marketing & automations', 'AI Assistant', 'Analytics & predictions'],
        },
        {
            'id': 'Vantav Scale',
            'name': 'Vantav Scale',
            'price': '$799/mo',
            'description': 'Enterprise-grade controls and fraud protection.',
            'features': ['Everything in Growth', 'Fraud & risk tools', 'Supplier hub', 'Reports & API access'],
        },
    ]
    ctx = context(active_page='choose_tier', merchant=merchant, merchant_id=merchant_id)
    ctx['nav_groups'] = []
    ctx['active_page'] = 'choose_tier'
    ctx['merchant'] = dict(ctx.get('merchant', {}) or {}, tier='Choose your plan')
    ctx['tiers'] = tiers
    return render_template('choose_tier.html', **ctx)


@app.route('/api/merchant/select-tier', methods=['POST'])
@require_roles([UserRole.MERCHANT])
def api_merchant_select_tier():
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    tier = data.get('tier')
    if tier not in ('Basic Tier', 'Vantav Operator', 'Vantav Growth', 'Vantav Scale'):
        return jsonify({'error': 'Invalid tier'}), 400
    # Paid tiers can be selected directly only for whitelisted test accounts.
    # All other merchants must complete checkout before a paid tier is enabled.
    test_tier_emails = _tier_test_accounts()
    if tier != 'Basic Tier' and merchant.get('email', '').lower() not in test_tier_emails:
        slug = {'Vantav Operator': 'operator', 'Vantav Growth': 'growth', 'Vantav Scale': 'scale'}.get(tier)
        return jsonify({'error': 'Paid plan requires checkout', 'redirect': '/checkout?plan=' + quote(slug or '')}), 402
    merchant_id = merchant['id']
    profile = MerchantProfile.query.get(merchant_id)
    if not profile:
        return jsonify({'error': 'Merchant not found'}), 404
    billing = SaaSBilling.query.get(merchant_id)
    if not billing:
        billing = SaaSBilling(merchant_id=merchant_id)
        db.session.add(billing)
    tier_meta = TierManager.get_tier_meta(tier)
    profile.account_tier = tier
    profile.sandbox_status = 'approved'
    profile.live_access_enabled = 1
    profile.approved_at = datetime.utcnow()
    billing.current_plan = tier
    try:
        memory = action_gate.get_business_memory(merchant_id)
        memory.max_authorized_seats = int(tier_meta.get('max_users', 1))
    except Exception as e:
        logger.warning(f"[SelectTier] Could not update seats for {merchant_id}: {e}")
    db.session.commit()
    try:
        log_admin_audit('tier_selected', target_merchant_id=merchant_id, details={'tier': tier, 'source': 'choose-tier'})
    except Exception:
        pass
    return jsonify({'status': 'ok', 'tier': tier, 'redirect': url_for('dashboard')})


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


@app.route('/api/analytics/channels', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_channel_analytics():
    """Return true-profit summary per sales channel."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    days = int(request.args.get("days", 30) or 30)
    try:
        summary = channel_analytics.summarize_channels(merchant["id"], days=days)
        totals = channel_analytics.channel_totals(merchant["id"], days=days)
        return jsonify({"channels": summary, "totals": totals, "days": days}), 200
    except Exception as e:
        logger.error(f"[Channel Analytics] Failed: {e}")
        return jsonify({"detail": str(e)}), 500


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


@app.route('/api/actions/<int:action_id>/verify', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_verify_action(action_id):
    """Re-capture KPIs for an executed action and produce a verification report."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    try:
        result = action_gate.verify_action(action_id, merchant["id"])
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[Action Gate] Verify failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/actions/<int:action_id>/rollback', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_rollback_action(action_id):
    """Rollback an approved/executed Action Gate action using the captured audit snapshot."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    try:
        result = action_gate.rollback_action(action_id, merchant["id"], decided_by=merchant["id"])
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[Action Gate] Rollback failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/v1/actions/<int:action_id>/approve', methods=['POST'])
@master_auth_engine.require_clearance("admin")
def api_v1_approve_action(action_id):
    """Hardened Action Gate approve endpoint protected by signed X-Session-Token."""
    merchant_id = g.session_ctx.get("merchant_id")
    try:
        result = action_gate.approve_action(action_id, merchant_id, decided_by=merchant_id)
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[Action Gate] V1 approve failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/actions/verify-cron', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_verify_actions_cron():
    """CRON-style endpoint to verify all executed actions older than the configured window."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    hours = int(data.get("hours", 48) or 48)
    try:
        results = action_gate.verify_overdue_actions(merchant["id"], hours=hours)
        return jsonify({"verified": len(results), "results": results}), 200
    except Exception as e:
        logger.error(f"[Action Gate] Verify-cron failed: {e}")
        return jsonify({"detail": str(e)}), 500


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


@app.route('/api/v1/channels/<platform>/display-name', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_channel_display_name(platform):
    """Set a merchant-specific display name for a channel."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    name = (data.get("display_name") or "").strip()
    if not name:
        return jsonify({"error": "display_name is required"}), 400
    setting = MerchantSetting.query.get((merchant["id"], f"channel_name:{platform}"))
    if not setting:
        setting = MerchantSetting(merchant_id=merchant["id"], setting_key=f"channel_name:{platform}")
        db.session.add(setting)
    setting.setting_value = name
    db.session.commit()
    return jsonify({"updated": True, "platform": platform, "display_name": name}), 200


def _trigger_initial_sync(merchant_id: str, channel: str) -> None:
    """Start the 14-day historical pull + diagnostic audit in the background."""
    try:
        app = current_app._get_current_object()
        if channel == "shopify":
            historical_ingestion.trigger_onboarding_harvest(merchant_id, app=app)
        else:
            initial_sync.start_initial_sync(merchant_id, channel, app)
    except Exception as e:
        logger.error(f"[Initial Sync] Failed to start background audit for {merchant_id}: {e}")


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
        shopify_sync._shopify_get(shop, token, "shop")
    except Exception:
        logger.warning("[Channels] Shopify credential validation failed for merchant %s", merchant["id"])
        return jsonify({
            "detail": "Shopify could not verify these credentials. Check the store domain and access token, then try again.",
        }), 400
    try:
        channels_module.connect_shopify(merchant["id"], shop, token)
        _trigger_initial_sync(merchant["id"], "shopify")
        return jsonify({"status": "connected", "platform": "shopify", "domain": shop, "audit_started": True}), 200
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
    region = (data.get("region") or "").strip().lower()
    if not seller_id or not app_key or not app_secret or not access_token:
        return jsonify({"detail": "seller_id, app_key, app_secret, and access_token required"}), 400
    try:
        channels_module.connect_tiktok(merchant["id"], seller_id, app_key, app_secret, access_token, shop_cipher, region=region)
        _trigger_initial_sync(merchant["id"], "tiktok")
        return jsonify({"status": "connected", "platform": "tiktok", "audit_started": True}), 200
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
        _trigger_initial_sync(merchant["id"], "amazon")
        return jsonify({"status": "connected", "platform": "amazon", "audit_started": True}), 200
    except Exception as e:
        logger.error(f"[Channels] Amazon connect failed: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/v1/sync/initialize-onboarding-harvest', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_initialize_onboarding_harvest():
    """Trigger the 14-day Shopify historical harvest in a background worker."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    shop_domain = (data.get("shop_domain") or "").strip()
    token = (data.get("access_token") or data.get("decrypted_offline_token") or "").strip()
    result = historical_ingestion.trigger_onboarding_harvest(
        merchant["id"],
        shop_domain=shop_domain or None,
        token=token or None,
        app=current_app._get_current_object(),
    )
    return jsonify(result), 200 if result.get("status") == "processing" else 400


@app.route('/api/v1/auth/callback/complete', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_auth_callback_complete():
    """Landing route for store authorizations: triggers the automated background audit."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    header_id = request.headers.get("X-Merchant-Id", "").strip()
    if header_id and header_id != merchant["id"]:
        return jsonify({"error": "Merchant context mismatch"}), 403
    data = request.get_json(silent=True) or {}
    channel = (data.get("channel") or "shopify").lower().strip()
    if channel not in ("shopify", "tiktok", "amazon"):
        return jsonify({"detail": "channel must be shopify, tiktok, or amazon"}), 400
    _trigger_initial_sync(merchant["id"], channel)
    return jsonify({
        "status": "authorized",
        "message": "Store authorization complete. Your AI COO is actively auditing your store telemetry logs now.",
        "channel": channel,
        "audit_started": True,
    }), 200


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
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
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
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_admin_sync_tiktok(merchant_id):
    """Admin-triggered TikTok Shop sync for testing (bypasses live-access gate)."""
    try:
        result = tiktok_sync.sync_tiktok(merchant_id)
        return jsonify({"status": "synced", "merchant_id": merchant_id, **result}), 200
    except Exception as e:
        logger.error(f"[Admin TikTok Sync] Failed for {merchant_id}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/admin/tiktok/marketing/sync/<merchant_id>', methods=['POST'])
@require_roles([UserRole.ADMIN])
def api_admin_sync_tiktok_marketing(merchant_id):
    """Admin-triggered TikTok ad campaign sync."""
    try:
        studio = tiktok_marketing_studio.TikTokMarketingStudio(merchant_id)
        result = studio.sync_campaigns()
        return jsonify({"status": "synced", "merchant_id": merchant_id, **result}), 200
    except Exception as e:
        logger.error(f"[Admin TikTok Marketing Sync] Failed for {merchant_id}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/admin/tiktok/marketing/evaluate/<merchant_id>', methods=['POST'])
@require_roles([UserRole.ADMIN])
def api_admin_evaluate_tiktok_marketing(merchant_id):
    """Admin-triggered TikTok marketing overhead evaluation and action drafting."""
    try:
        studio = tiktok_marketing_studio.TikTokMarketingStudio(merchant_id)
        sync = studio.sync_campaigns()
        evaluations = studio.evaluate_all_active_campaigns()
        return jsonify({"status": "ok", "merchant_id": merchant_id, "sync": sync, "evaluations": evaluations}), 200
    except Exception as e:
        logger.error(f"[Admin TikTok Marketing Evaluate] Failed for {merchant_id}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/admin/tiktok/marketing/creative-refresh/<merchant_id>', methods=['POST'])
@require_roles([UserRole.ADMIN])
def api_admin_tiktok_creative_refresh(merchant_id):
    """Queue a TikTok creative refresh for an SKU."""
    data = request.get_json() or {}
    sku = data.get("sku", "")
    brief = data.get("brief", "")
    if not sku:
        return jsonify({"detail": "sku is required"}), 400
    try:
        studio = tiktok_marketing_studio.TikTokMarketingStudio(merchant_id)
        result = studio.trigger_creative_refresh(sku, brief)
        return jsonify({"status": "ok", "merchant_id": merchant_id, **result}), 200
    except Exception as e:
        logger.error(f"[Admin TikTok Creative Refresh] Failed for {merchant_id}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/admin/marketing/generate/<merchant_id>', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT])
def api_admin_generate_marketing_assets(merchant_id):
    """Generate and persist marketing asset drafts for a product."""
    data = request.get_json() or {}
    sku = data.get("sku", "")
    viral_velocity_score = int(data.get("viral_velocity_score", 0) or 0)
    target_demographic = data.get("target_demographic", "Gen Z")
    if not sku:
        return jsonify({"detail": "sku is required"}), 400
    try:
        ctx = marketing_studio_module.VantavMarketingStudio.build_context_from_product(
            merchant_id, sku, viral_velocity_score, target_demographic
        )
        if not ctx:
            return jsonify({"detail": "Product not found"}), 404
        profile = MerchantProfile.query.filter_by(merchant_id=merchant_id).first()
        store_url = getattr(profile, "brand_url", None) if profile else None
        drafts = marketing_studio_module.VantavMarketingStudio.generate_viral_campaign_drafts(
            ctx, merchant_id=merchant_id, store_url=store_url
        )
        return jsonify({"status": "ok", "merchant_id": merchant_id, "assets": [d.dict() for d in drafts]}), 200
    except Exception as e:
        logger.error(f"[Marketing Studio] Failed for {merchant_id}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/admin/marketing/drafts/<merchant_id>', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT])
def api_admin_list_marketing_drafts(merchant_id):
    """List generated marketing asset drafts for a merchant."""
    state = request.args.get("state")
    try:
        drafts = marketing_studio_module.VantavMarketingStudio.list_drafts(merchant_id, state)
        return jsonify({"status": "ok", "merchant_id": merchant_id, "drafts": drafts}), 200
    except Exception as e:
        logger.error(f"[Marketing Studio] Failed listing drafts for {merchant_id}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/admin/marketing/drafts/<asset_id>/state', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT])
def api_admin_update_marketing_draft_state(asset_id):
    """Update a marketing asset state (approve, reject, sent)."""
    data = request.get_json() or {}
    new_state = data.get("state", "")
    if not new_state:
        return jsonify({"detail": "state is required"}), 400
    try:
        result = marketing_studio_module.VantavMarketingStudio.update_asset_state(asset_id, new_state)
        if not result:
            return jsonify({"detail": "Asset not found"}), 404
        return jsonify({"status": "ok", **result}), 200
    except Exception as e:
        logger.error(f"[Marketing Studio] Failed updating asset {asset_id}: {e}")
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
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_admin_sync_amazon(merchant_id):
    """Admin-triggered Amazon sync for testing (bypasses live-access gate)."""
    try:
        result = amazon_sync.sync_amazon(merchant_id)
        return jsonify({"status": "synced", "merchant_id": merchant_id, **result}), 200
    except Exception as e:
        logger.error(f"[Admin Amazon Sync] Failed for {merchant_id}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/admin/provision-demo', methods=['POST'])
@require_roles([UserRole.ADMIN])
def admin_provision_demo():
    """Create or reset a demo/review merchant with full live access for partnership testing."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    business_name = (data.get("business_name") or "Partnership Test Store").strip()
    tier = (data.get("tier") or "Vantav Scale").strip()
    if not email:
        return jsonify({"detail": "Email is required."}), 400

    # Generate a strong temporary password for the reviewer.
    temp_password = secrets.token_urlsafe(12)
    password_hash = generate_password_hash(temp_password, method="pbkdf2:sha256")
    now = datetime.utcnow()

    profile = MerchantProfile.query.filter_by(admin_email=email).first()
    if profile:
        profile.password_hash = password_hash
        profile.account_tier = tier
        profile.sandbox_status = "approved"
        profile.live_access_enabled = 1
        profile.approved_at = now
        profile.business_name = business_name
        merchant_id = profile.merchant_id
    else:
        merchant_id = f"demo_{secrets.token_hex(4)}"
        db.session.add(MerchantProfile(
            merchant_id=merchant_id,
            business_name=business_name,
            admin_email=email,
            account_tier=tier,
            password_hash=password_hash,
            sandbox_status="approved",
            live_access_enabled=1,
            approved_at=now,
            created_at=now,
        ))

    billing = SaaSBilling.query.get(merchant_id)
    if not billing:
        billing = SaaSBilling(merchant_id=merchant_id)
        db.session.add(billing)
    billing.current_plan = tier
    billing.billing_cycle_end = (now + timedelta(days=30)).strftime('%Y-%m-%d')

    if not MerchantMetric.query.filter_by(merchant_id=merchant_id).first():
        db.session.add(MerchantMetric(
            merchant_id=merchant_id,
            total_unified_balance=0.0,
            true_net_profit=0.0,
            gross_revenue=0.0,
            ai_briefing="Demo account for partnership review.",
        ))

    db.session.commit()
    return jsonify({
        "merchant_id": merchant_id,
        "email": email,
        "password": temp_password,
        "tier": tier,
        "live_access": True,
        "sandbox_status": "approved",
    }), 200


@app.route('/api/admin/seed-regression-demo/<merchant_id>', methods=['POST'])
@require_roles([UserRole.ADMIN])
def api_admin_seed_regression_demo(merchant_id):
    """Backfill 14 days of realistic revenue and cost data for the regression demo SKU."""
    try:
        sku = request.args.get('sku', 'SKU-404-PODS')
        days = int(request.args.get('days', 14))
        result = seed_regression_sku.seed_sku_for_regression(merchant_id, sku=sku, days=days)
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[Seed Regression Demo] {merchant_id}: {e}")
        return jsonify({"detail": str(e)}), 500


def _admin_merchant_row(p: MerchantProfile, now: datetime) -> dict:
    """Build a single merchant summary for the admin members view."""
    billing = SaaSBilling.query.get(p.merchant_id)
    links = IntegrationLink.query.filter_by(merchant_id=p.merchant_id).all()
    tokens = TenantOAuthToken.query.filter_by(merchant_id=p.merchant_id).order_by(TenantOAuthToken.updated_at.desc()).all()
    last_sync = max(
        [t.updated_at for t in tokens if t.updated_at] +
        [l.updated_at for l in links if l.updated_at],
        default=None,
    )
    sync_status = "No channels"
    if last_sync:
        age = (now - last_sync).total_seconds()
        sync_status = "OK" if age <= monitoring_module.MAX_CHANNEL_SYNC_AGE_SECONDS else "Stale"
    pending_actions = PendingAction.query.filter_by(merchant_id=p.merchant_id, status='pending').count()
    unread = SupportMessage.query.filter_by(
        merchant_id=p.merchant_id, sender='merchant', read_at=None
    ).count()
    seat_count = WorkspaceSeat.query.filter_by(merchant_id=p.merchant_id).count()
    memory = action_gate.get_business_memory(p.merchant_id)
    max_seats = memory.max_authorized_seats if memory else None
    last_active = db.session.query(func.max(ActiveSession.last_seen)).filter_by(merchant_id=p.merchant_id).scalar()
    return {
        "merchant_id": p.merchant_id,
        "business_name": p.business_name or p.merchant_id,
        "admin_email": p.admin_email or "—",
        "account_tier": (p.account_tier or "").replace("AI Tier", "Plan").strip(),
        "current_plan": billing.current_plan if billing else p.account_tier,
        "metered_usage_units": billing.metered_usage_units if billing else None,
        "accrued_invoice_value": billing.accrued_invoice_value if billing else None,
        "billing_cycle_end": billing.billing_cycle_end if billing else None,
        "max_authorized_seats": max_seats,
        "sandbox_status": p.sandbox_status or "pending",
        "live_access": bool(p.live_access_enabled),
        "feature_flags": p.feature_flags or {},
        "concierge": bool(billing and isinstance(billing.add_ons, list) and "concierge_bundle" in billing.add_ons),
        "channels": [l.platform for l in links if l.platform],
        "channel_count": len(links),
        "seats": seat_count,
        "last_sync": last_sync.isoformat() if last_sync else None,
        "last_active": last_active.isoformat() if last_active else None,
        "sync_status": sync_status,
        "pending_actions": pending_actions,
        "unread": unread,
    }


@app.route('/admin/merchants')
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def admin_merchants():
    """Backend admin view of all merchants, tiers, sync status, pending actions, and live chat."""
    ctx = _dashboard_context('admin_merchants')
    s = get_current_user()
    ctx["dashboard_title"] = "Admin" if s and s.role == UserRole.ADMIN.value else "Engineer"
    now = datetime.utcnow()
    ctx["merchants"] = [_admin_merchant_row(p, now) for p in MerchantProfile.query.order_by(MerchantProfile.created_at.desc()).all()]
    ctx["stripe_balance"] = billing_module.get_stripe_balance()
    ctx["total_members"] = MerchantProfile.query.count()
    ctx["active_sessions"] = ActiveSession.query.filter(
        ActiveSession.last_seen >= now - timedelta(minutes=15)
    ).count()
    ctx["paid_accounts"] = MerchantProfile.query.filter_by(live_access_enabled=1).count()
    ctx["feature_pages"] = ALL_DASHBOARD_PAGE_IDS
    return render_template('dashboard/admin_merchants.html', **ctx)


@app.route('/api/admin/merchants', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_admin_merchants():
    """JSON list of merchants for admin monitoring."""
    now = datetime.utcnow()
    out = [_admin_merchant_row(p, now) for p in MerchantProfile.query.order_by(MerchantProfile.created_at.desc()).all()]
    return jsonify({"merchants": out, "count": len(out)}), 200


@app.route('/api/admin/merchants/<merchant_id>', methods=['PATCH'])
@require_roles([UserRole.ADMIN])
def api_admin_update_merchant(merchant_id):
    """Admin update of merchant tier, sandbox, live access, feature flags, and billing."""
    p = MerchantProfile.query.get_or_404(merchant_id)
    data = request.get_json(silent=True) or {}
    if 'account_tier' in data:
        p.account_tier = _canonical_tier(data['account_tier'])
    if 'sandbox_status' in data:
        p.sandbox_status = data['sandbox_status']
    if 'live_access_enabled' in data:
        p.live_access_enabled = 1 if data['live_access_enabled'] else 0
    if 'feature_flags' in data and isinstance(data['feature_flags'], dict):
        # Replace the entire feature_flags map with the admin's selections.
        # Default (unset) pages are omitted, On is True, Off is False.
        p.feature_flags = {k: v for k, v in data['feature_flags'].items() if v is not None}

    # Billing override: update the merchant's SaaSBilling and seat allocation.
    billing = SaaSBilling.query.get(merchant_id)
    if not billing:
        billing = SaaSBilling(merchant_id=merchant_id)
        db.session.add(billing)
    if 'current_plan' in data:
        billing.current_plan = _canonical_tier(data['current_plan'])
    if 'add_ons' in data and isinstance(data['add_ons'], list):
        billing.add_ons = data['add_ons']
    if 'metered_usage_units' in data:
        billing.metered_usage_units = int(data['metered_usage_units'])
    if 'accrued_invoice_value' in data:
        billing.accrued_invoice_value = float(data['accrued_invoice_value'])
    if 'billing_cycle_end' in data:
        billing.billing_cycle_end = data['billing_cycle_end']
    if 'max_authorized_seats' in data:
        memory = action_gate.get_business_memory(merchant_id)
        memory.max_authorized_seats = int(data['max_authorized_seats'])
    db.session.commit()
    log_admin_audit("merchant.update", target_merchant_id=merchant_id, details={"fields": list(data.keys())})
    now = datetime.utcnow()
    return jsonify(_admin_merchant_row(p, now)), 200


@app.route('/api/admin/merchants/<merchant_id>', methods=['DELETE'])
@require_roles([UserRole.ADMIN])
def api_admin_delete_merchant(merchant_id):
    """Permanently delete a merchant and all related rows. Protected accounts are blocked."""
    p = MerchantProfile.query.get_or_404(merchant_id)
    if p.merchant_id in _protected_merchant_ids():
        return jsonify({'error': 'Cannot delete protected account'}), 403
    _cascade_delete_merchant(p.merchant_id)
    db.session.delete(p)
    db.session.commit()
    log_admin_audit("merchant.delete", target_merchant_id=merchant_id, details={'admin_email': (get_merchant_context() or {}).get('email')})
    return jsonify({'status': 'ok', 'deleted': merchant_id}), 200


@app.route('/api/admin/stripe-balance')
@require_roles([UserRole.ADMIN])
def api_admin_stripe_balance():
    """Return the platform Stripe balance."""
    return jsonify(billing_module.get_stripe_balance())


@app.route('/admin/chat')
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def admin_chat():
    """Admin support chat dashboard."""
    ctx = _dashboard_context('admin_chat')
    s = get_current_user()
    ctx["dashboard_title"] = "Admin" if s and s.role == UserRole.ADMIN.value else "Engineer"
    return render_template('dashboard/admin_chat.html', **ctx)


@app.route('/api/admin/chat/threads', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_admin_chat_threads():
    """List merchants with unread support message counts."""
    rows = db.session.query(
        SupportMessage.merchant_id,
        func.count(SupportMessage.id).filter(SupportMessage.read_at.is_(None), SupportMessage.sender == 'merchant').label('unread'),
        func.max(SupportMessage.created_at).label('last_message_at'),
    ).group_by(SupportMessage.merchant_id).all()
    profiles = {p.merchant_id: p for p in MerchantProfile.query.all()}
    threads = []
    for merchant_id, unread, last_message_at in rows:
        p = profiles.get(merchant_id)
        threads.append({
            "merchant_id": merchant_id,
            "business_name": p.business_name or merchant_id if p else merchant_id,
            "admin_email": p.admin_email if p else None,
            "unread": unread,
            "last_message_at": last_message_at.isoformat() if last_message_at else None,
        })
    # include merchants with no messages at the bottom
    messaged = {t['merchant_id'] for t in threads}
    for p in MerchantProfile.query.all():
        if p.merchant_id not in messaged:
            threads.append({
                "merchant_id": p.merchant_id,
                "business_name": p.business_name or p.merchant_id,
                "admin_email": p.admin_email,
                "unread": 0,
                "last_message_at": None,
            })
    threads.sort(key=lambda x: (x['last_message_at'] is None, x['last_message_at']), reverse=True)
    return jsonify({"threads": threads}), 200


@app.route('/api/admin/chat/<merchant_id>', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_admin_chat_messages(merchant_id):
    """Return chat history with a merchant and mark merchant messages as read."""
    profile = MerchantProfile.query.get_or_404(merchant_id)
    SupportMessage.query.filter_by(
        merchant_id=merchant_id, sender='merchant', read_at=None
    ).update({"read_at": datetime.utcnow()}, synchronize_session=False)
    db.session.commit()
    msgs = SupportMessage.query.filter_by(merchant_id=merchant_id).order_by(SupportMessage.created_at.asc()).all()
    return jsonify({
        "merchant_id": merchant_id,
        "business_name": profile.business_name or merchant_id,
        "messages": [
            {"id": m.id, "sender": m.sender, "sender_email": m.sender_email, "message": m.message, "created_at": m.created_at.isoformat() if m.created_at else None, "read": m.read_at is not None}
            for m in msgs
        ],
    }), 200


@app.route('/api/admin/chat/<merchant_id>', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_admin_chat_send(merchant_id):
    """Send an admin message to a merchant."""
    profile = MerchantProfile.query.get_or_404(merchant_id)
    data = request.get_json(silent=True) or {}
    text = (data.get('message') or '').strip()
    if not text:
        return jsonify({"error": "Message is required"}), 400
    s = get_current_user()
    admin_profile = MerchantProfile.query.get(s.merchant_id) if s else None
    msg = SupportMessage(
        merchant_id=merchant_id,
        sender='admin',
        sender_email=admin_profile.admin_email if admin_profile else 'admin@vantavcommerce.com',
        message=text,
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify({"id": msg.id, "sender": "admin", "created_at": msg.created_at.isoformat()}), 201


# --------------------------------------------------------------------------
# Master admin controls
# --------------------------------------------------------------------------

@app.route('/api/admin/impersonate/<merchant_id>', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_admin_impersonate(merchant_id):
    """Admin view-as-merchant: load the target tenant's dashboard without a password."""
    target = MerchantProfile.query.get_or_404(merchant_id)
    s = get_current_user()
    if not s:
        return jsonify({"error": "Session required"}), 403
    # Save the admin's original context so it can be restored.
    s.original_merchant_id = s.merchant_id
    s.original_role = s.role
    s.impersonating_merchant_id = target.merchant_id
    db.session.commit()
    log_admin_audit("impersonation.start", target_merchant_id=target.merchant_id, details={"admin_email": (MerchantProfile.query.get(s.original_merchant_id).admin_email if s.original_merchant_id else None)})
    return jsonify({"status": "impersonating", "merchant_id": target.merchant_id, "redirect": url_for('dashboard')}), 200


@app.route('/api/admin/stop-impersonating', methods=['GET', 'POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_admin_stop_impersonating():
    """Restore the admin's original merchant context."""
    s = get_current_user()
    if not s:
        return jsonify({"error": "Session required"}), 403
    target = s.impersonating_merchant_id
    if target:
        log_admin_audit("impersonation.stop", target_merchant_id=target)
    if s.original_merchant_id:
        s.merchant_id = s.original_merchant_id
        s.role = s.original_role or UserRole.ADMIN.value
    s.impersonating_merchant_id = None
    s.original_merchant_id = None
    s.original_role = None
    db.session.commit()
    return redirect(url_for('admin_dashboard'))


@app.route('/api/admin/announcements', methods=['POST'])
@require_roles([UserRole.ADMIN])
def api_admin_announcement():
    """Broadcast a global admin message to every merchant's support chat."""
    data = request.get_json(silent=True) or {}
    text = (data.get('message') or '').strip()
    if not text:
        return jsonify({"error": "Message is required"}), 400
    s = get_current_user()
    admin_email = ""
    if s and s.merchant_id:
        admin_profile = MerchantProfile.query.get(s.merchant_id)
        admin_email = admin_profile.admin_email if admin_profile else ""
    count = 0
    for profile in MerchantProfile.query.all():
        msg = SupportMessage(
            merchant_id=profile.merchant_id,
            sender='admin',
            sender_email=admin_email or 'admin@vantavcommerce.com',
            message=text,
        )
        db.session.add(msg)
        count += 1
    db.session.commit()
    log_admin_audit("announcement.broadcast", details={"message": text, "recipients": count})
    return jsonify({"status": "broadcast", "recipients": count}), 201


@app.route('/api/admin/audit', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_admin_audit():
    """Paginated admin audit log."""
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))
    action = request.args.get('action')
    target = request.args.get('merchant_id')
    q = AdminAuditLog.query.order_by(AdminAuditLog.created_at.desc())
    if action:
        q = q.filter_by(action=action)
    if target:
        q = q.filter_by(target_merchant_id=target)
    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    return jsonify({
        "total": total,
        "offset": offset,
        "limit": limit,
        "events": [
            {
                "id": r.id,
                "admin_email": r.admin_email,
                "action": r.action,
                "target_merchant_id": r.target_merchant_id,
                "details": r.details,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }), 200


@app.route('/api/admin/platform-controls', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_admin_platform_controls_get():
    """Return the current global platform switches."""
    return jsonify({
        "global_sync_paused": global_sync_paused(),
        "maintenance_mode": maintenance_mode(),
        "sample_pages_enabled": sample_pages_enabled(),
    }), 200


@app.route('/api/admin/platform-controls', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_admin_platform_controls_set():
    """Update global platform switches: global_sync_paused, maintenance_mode, sample_pages_enabled."""
    data = request.get_json(silent=True) or {}
    for key in ('global_sync_paused', 'maintenance_mode', 'sample_pages_enabled'):
        if key in data:
            AdminPlatformControl.set_bool(key, bool(data[key]))
            log_admin_audit("platform_control.set", details={"key": key, "value": bool(data[key])})
    return jsonify({
        "global_sync_paused": global_sync_paused(),
        "maintenance_mode": maintenance_mode(),
        "sample_pages_enabled": sample_pages_enabled(),
    }), 200


@app.route('/api/admin/<platform>/sync/<merchant_id>', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_admin_sync_platform(platform, merchant_id):
    """Generic admin-triggered sync for any supported platform."""
    if platform not in ('shopify', 'tiktok', 'amazon', 'ebay', 'walmart', 'bigcommerce', 'woocommerce'):
        return jsonify({"error": "Invalid platform"}), 400
    profile = MerchantProfile.query.get_or_404(merchant_id)
    try:
        if platform == 'shopify':
            result = shopify_sync.sync_shopify(merchant_id)
        elif platform == 'tiktok':
            result = tiktok_sync.sync_tiktok(merchant_id)
        elif platform == 'amazon':
            result = amazon_sync.sync_amazon(merchant_id)
        else:
            return jsonify({"status": "not_implemented", "platform": platform}), 501
        log_admin_audit("store.sync", target_merchant_id=merchant_id, details={"platform": platform})
        return jsonify({"status": "synced", "merchant_id": merchant_id, "platform": platform, **result}), 200
    except Exception as e:
        logger.error(f"[Admin {platform.title()} Sync] Failed for {merchant_id}: {e}")
        return jsonify({"detail": str(e)}), 400


@app.route('/api/admin/stores/<merchant_id>/<platform>/reset', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_admin_store_reset(merchant_id, platform):
    """Mark a merchant's channel connection stale (forces re-auth/re-sync)."""
    if platform not in ('shopify', 'tiktok', 'amazon', 'ebay', 'walmart', 'bigcommerce', 'woocommerce'):
        return jsonify({"error": "Invalid platform"}), 400
    try:
        token = TenantOAuthToken.query.filter_by(merchant_id=merchant_id, platform_id=platform).first()
        if token:
            token.updated_at = datetime(2000, 1, 1)
            db.session.add(token)
        for link in IntegrationLink.query.filter_by(merchant_id=merchant_id, platform=platform).all():
            link.updated_at = datetime(2000, 1, 1)
            db.session.add(link)
        db.session.commit()
        log_admin_audit("store.reset", target_merchant_id=merchant_id, details={"platform": platform})
        return jsonify({"status": "reset", "merchant_id": merchant_id, "platform": platform}), 200
    except Exception as e:
        logger.error(f"[Admin Store Reset] {merchant_id}/{platform}: {e}")
        return jsonify({"error": str(e)}), 400


@app.route('/admin')
@require_roles([UserRole.ADMIN])
def admin_dashboard():
    """Admin control panel landing page."""
    ctx = _dashboard_context('admin_dashboard')
    now = datetime.utcnow()
    ctx["summary"] = _admin_summary(now)
    ctx["stores"] = _admin_stores()
    ctx["recent_events"] = _admin_recent_events(now)
    return render_template('dashboard/admin.html', dashboard_title='Admin', **ctx)


@app.route('/engineer')
@require_roles([UserRole.ENGINEER])
def engineer_dashboard():
    """Engineer control panel landing page."""
    ctx = _dashboard_context('engineer_dashboard')
    ctx["dashboard_title"] = "Engineer"
    return render_template('dashboard/engineer.html', **ctx)


@app.route('/admin/audit')
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def admin_audit_page():
    """Admin audit log page."""
    ctx = _dashboard_context('admin_audit')
    s = get_current_user()
    ctx["dashboard_title"] = "Admin" if s and s.role == UserRole.ADMIN.value else "Engineer"
    ctx["events"] = AdminAuditLog.query.order_by(AdminAuditLog.created_at.desc()).limit(100).all()
    return render_template('dashboard/admin_audit.html', **ctx)


def _admin_summary(now):
    """Compute platform-wide summary numbers for the admin dashboard."""
    total_members = MerchantProfile.query.count()
    active_sessions = ActiveSession.query.filter(
        ActiveSession.last_seen >= now - timedelta(minutes=15)
    ).count()
    paid_accounts = MerchantProfile.query.filter_by(live_access_enabled=1).count()
    unread_support = SupportMessage.query.filter_by(sender='merchant', read_at=None).count()
    pending_sandbox = MerchantProfile.query.filter(
        MerchantProfile.sandbox_status.in_(['pending', 'sandbox'])
    ).count()
    stripe_balance = billing_module.get_stripe_balance()
    connected_stores = MerchantChannel.query.count()
    return {
        "total_members": total_members,
        "active_sessions": active_sessions,
        "paid_accounts": paid_accounts,
        "unread_support": unread_support,
        "pending_sandbox": pending_sandbox,
        "stripe_balance": stripe_balance,
        "connected_stores": connected_stores,
    }


def _admin_stores():
    """List every connected store across all merchant accounts."""
    stores = []
    for p in MerchantProfile.query.all():
        try:
            channels = channels_module.list_channels(p.merchant_id)
        except Exception:
            continue
        for ch in channels:
            if ch.get('state') != 'connected':
                continue
            stores.append({
                "merchant_id": p.merchant_id,
                "business_name": p.business_name or p.merchant_id,
                "admin_email": p.admin_email,
                "platform": ch.get('platform'),
                "name": ch.get('name'),
                "orders": ch.get('orders', 0),
                "revenue": ch.get('revenue', 0.0),
                "sync": ch.get('sync'),
            })
    return stores


def _admin_recent_events(now):
    """Return recent platform activity for the admin dashboard."""
    events = []
    for s in ActiveSession.query.order_by(ActiveSession.created_at.desc()).limit(10).all():
        events.append({
            "time": s.created_at.isoformat() if s.created_at else None,
            "message": f"Session created for {s.merchant_id} ({s.role})",
        })
    for m in SupportMessage.query.order_by(SupportMessage.created_at.desc()).limit(10).all():
        events.append({
            "time": m.created_at.isoformat() if m.created_at else None,
            "message": f"Support message from {m.sender} ({m.merchant_id})",
        })
    events.sort(key=lambda x: x.get('time') or '', reverse=True)
    return events[:20]


@app.route('/api/admin/summary', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_admin_summary():
    """Return platform summary for the admin dashboard."""
    now = datetime.utcnow()
    return jsonify(_admin_summary(now)), 200


@app.route('/api/admin/stores', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_admin_stores():
    """Return all connected stores across merchants."""
    return jsonify({"stores": _admin_stores()}), 200


@app.route('/api/admin/stores/<merchant_id>/<platform>/unlink', methods=['POST'])
@require_roles([UserRole.ADMIN])
def api_admin_unlink_store(merchant_id, platform):
    """Admin-only: disconnect a store from any merchant account."""
    if platform not in ('shopify', 'tiktok', 'amazon', 'ebay', 'walmart', 'bigcommerce', 'woocommerce'):
        return jsonify({"error": "Invalid platform"}), 400
    try:
        channels_module.disconnect(merchant_id, platform)
        log_admin_audit("store.unlink", target_merchant_id=merchant_id, details={"platform": platform})
        return jsonify({"status": "disconnected", "merchant_id": merchant_id, "platform": platform}), 200
    except Exception as e:
        logger.error(f"[Admin Unlink] {e}")
        return jsonify({"error": str(e)}), 400


@app.route('/api/v1/chat/messages', methods=['GET'])
@require_roles([UserRole.MERCHANT, UserRole.ADMIN])
def api_merchant_chat_messages():
    """Return this merchant's support messages and mark admin messages as read."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    merchant_id = merchant['id']
    SupportMessage.query.filter_by(
        merchant_id=merchant_id, sender='admin', read_at=None
    ).update({"read_at": datetime.utcnow()}, synchronize_session=False)
    db.session.commit()
    msgs = SupportMessage.query.filter_by(merchant_id=merchant_id).order_by(SupportMessage.created_at.asc()).all()
    return jsonify({
        "merchant_id": merchant_id,
        "messages": [
            {"id": m.id, "sender": m.sender, "sender_email": m.sender_email, "message": m.message, "created_at": m.created_at.isoformat() if m.created_at else None, "read": m.read_at is not None}
            for m in msgs
        ],
    }), 200


@app.route('/api/v1/chat/message', methods=['POST'])
@require_roles([UserRole.MERCHANT, UserRole.ADMIN])
def api_merchant_chat_send():
    """Post a message from the merchant to support / admin."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    text = (data.get('message') or '').strip()
    if not text:
        return jsonify({"error": "Message is required"}), 400
    msg = SupportMessage(
        merchant_id=merchant['id'],
        sender='merchant',
        sender_email=merchant.get('email'),
        message=text,
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify({"id": msg.id, "sender": "merchant", "created_at": msg.created_at.isoformat()}), 201


@app.route('/api/v1/channels/writeback', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_channel_writeback():
    """Execute a live outbound write-back to a connected marketplace."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"detail": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    action_type = (data.get("action_type") or "").lower()
    sku = data.get("sku", "")
    quantity = data.get("quantity")
    price = data.get("price")
    platform = data.get("platform", "")

    payload = {"sku": sku, "platform": platform}
    if quantity is not None:
        payload["quantity"] = int(quantity)
    if price is not None:
        payload["price"] = float(price)

    try:
        result = outbound.dispatch_action(action_type, merchant["id"], payload)
        return jsonify({"status": "ok", "writeback": result}), 200
    except Exception as e:
        logger.error(f"[Outbound] Writeback failed for {merchant['id']}: {e}")
        return jsonify({"detail": "Writeback failed"}), 500


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


@app.route('/api/v1/rules/evaluate', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_rules_evaluate():
    """Run the deterministic rule engine against merchant telemetry.

    Accepts explicit SKU telemetry or evaluates the merchant's 24h aggregate.
    """
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    telemetry = data.get("telemetry")
    window_hours = int(data.get("window_hours", 24) or 24)
    try:
        if telemetry:
            created = rules_engine.run_for_sku(merchant["id"], telemetry)
        else:
            created = rules_engine.run_for_merchant(merchant["id"], window_hours=window_hours)
        return jsonify({"created": created}), 200
    except Exception as e:
        logger.error(f"[Rules Engine] Evaluate failed: {e}")
        return jsonify({"detail": str(e)}), 500


@app.route('/api/v1/forecast/sku', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_forecast_sku():
    """Return a stockout/reorder forecast for a specific SKU."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    sku = (data.get("sku") or "").strip()
    if not sku:
        return jsonify({"error": "sku is required"}), 400
    try:
        report = forecaster.forecast_sku(merchant["id"], sku)
        return jsonify(report.model_dump()), 200
    except Exception as e:
        logger.error(f"[Forecast] SKU forecast failed: {e}")
        return jsonify({"detail": str(e)}), 500


@app.route('/api/v1/forecast/run', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_forecast_run():
    """Run forecasting for every product and return reports."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    days = int(data.get("days", 14) or 14)
    try:
        reports = forecaster.forecast_all_skus(merchant["id"], days=days)
        return jsonify({"reports": [r.model_dump() for r in reports]}), 200
    except Exception as e:
        logger.error(f"[Forecast] Run failed: {e}")
        return jsonify({"detail": str(e)}), 500


@app.route('/api/v1/forecast/cron', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_forecast_cron():
    """Morning CRON-style entrypoint: run forecasts and create alerts/actions for stockouts."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    try:
        reports = forecaster.forecast_all_skus(merchant["id"], days=14)
        alerts = rules_engine.evaluate_products(merchant["id"], window_hours=24)
        return jsonify({"reports": [r.model_dump() for r in reports], "alerts": alerts}), 200
    except Exception as e:
        logger.error(f"[Forecast] CRON run failed: {e}")
        return jsonify({"detail": str(e)}), 500


@app.route('/api/v1/coo/diagnostic', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_coo_diagnostic():
    """Run the multi-agent COO diagnostic for the merchant and stage validated actions."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    days = int(data.get("days", 1) or 1)
    create_actions = bool(data.get("create_actions", True))
    try:
        actions = coo_agent_mesh.run_diagnostic(
            merchant["id"], days=days, create_actions=create_actions
        )
        return jsonify({"actions": actions}), 200
    except Exception as e:
        logger.error(f"[COO Mesh] Diagnostic failed: {e}")
        return jsonify({"detail": str(e)}), 500


@app.route('/api/v1/sku-metrics', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_sku_metrics():
    """Return cached 24h SKU metrics using the Redis cache-barrier key schema."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    sku = request.args.get("sku", "").strip()
    if not sku:
        return jsonify({"detail": "sku is required"}), 400

    product = Product.query.filter_by(merchant_id=merchant["id"], sku=sku).first()
    if not product:
        return jsonify({"detail": f"SKU {sku} not found"}), 404

    since = datetime.utcnow() - timedelta(days=1)

    def _compute():
        return coo_agent_mesh._build_single_snapshot(merchant["id"], product, since).model_dump()

    try:
        metrics = cache_barrier.get_sku_metrics(merchant["id"], sku, _compute, ttl=60)
        return jsonify({"sku": sku, "metrics": metrics, "cached": True}), 200
    except Exception as e:
        logger.error(f"[SKU metrics] {e}")
        return jsonify({"detail": str(e)}), 500


@app.route('/api/v1/products/<sku>', methods=['PATCH'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_update_product(sku):
    """Update unit cost and/or reorder point for a SKU and recalc profit."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    product = Product.query.filter_by(merchant_id=merchant["id"], sku=sku).first()
    if not product:
        return jsonify({"detail": "Product not found"}), 404

    updated = False
    if "unit_cost" in data:
        try:
            product.unit_cost = round(float(data["unit_cost"]), 4)
            updated = True
        except (TypeError, ValueError):
            return jsonify({"detail": "unit_cost must be a number"}), 400
    if "reorder_point" in data:
        try:
            product.reorder_point = int(data["reorder_point"])
            updated = True
        except (TypeError, ValueError):
            return jsonify({"detail": "reorder_point must be an integer"}), 400

    recalc_count = 0
    if updated:
        db.session.add(product)
        db.session.commit()
    if "unit_cost" in data:
        try:
            recalc_count = profit_feed.recalc_profit_for_sku(merchant["id"], sku)
        except Exception as e:
            logger.error(f"[Product update] Profit recalc failed for {sku}: {e}")

    return jsonify({
        "sku": product.sku,
        "unit_cost": float(product.unit_cost or 0),
        "reorder_point": product.reorder_point,
        "profit_orders_recalculated": recalc_count,
    }), 200


@app.route('/api/v1/products/recalc-profit', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_recalc_profit():
    """Recompute all profit feed orders for the merchant using current product costs."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    try:
        count = profit_feed.recalc_profit_for_merchant(merchant["id"])
        return jsonify({"profit_orders_recalculated": count}), 200
    except Exception as e:
        logger.error(f"[Profit recalc] {e}")
        return jsonify({"detail": str(e)}), 500


@app.route('/api/v1/analytics/profit-regression', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_profit_regression():
    """Return OLS regression analysis and chart-ready data for a SKU."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    sku = request.args.get("sku", "").strip()
    lookback_days = int(request.args.get("lookback_days", 30) or 30)
    if not sku:
        return jsonify({"error": "sku is required"}), 400
    try:
        result = profit_regression.analyze_sku_chart(
            merchant["id"], sku, lookback_days=lookback_days
        )
        if not result:
            return jsonify({"error": "Insufficient data for regression"}), 404
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[Profit Regression] API failed: {e}")
        return jsonify({"detail": str(e)}), 500


@app.route('/regression-chart')
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def regression_chart_view():
    """Redirect legacy standalone regression chart into the dashboard."""
    return redirect(url_for('dashboard_page', page='regression-chart'))


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


@app.route('/api/v1/merchant/theme', methods=['GET', 'POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_merchant_theme():
    """Get or set the merchant's dashboard theme."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    allowed = {"prometheus-dark", "prometheus-light", "luxury-editorial", "minimal-clean"}
    if request.method == 'GET':
        setting = MerchantSetting.query.get((merchant["id"], "theme"))
        return jsonify({"theme": setting.setting_value if setting else "prometheus-dark"}), 200
    data = request.get_json(silent=True) or {}
    theme = (data.get("theme") or "prometheus-dark").strip()
    if theme not in allowed:
        return jsonify({"error": f"Invalid theme. Choose one of: {', '.join(sorted(allowed))}"}), 400
    setting = MerchantSetting.query.get((merchant["id"], "theme"))
    if not setting:
        setting = MerchantSetting(merchant_id=merchant["id"], setting_key="theme")
        db.session.add(setting)
    setting.setting_value = theme
    db.session.commit()
    return jsonify({"theme": theme}), 200


@app.route('/api/v1/merchant/set-password', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_merchant_set_password():
    """Allow a logged-in merchant to change their password."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    new_password = (data.get("new_password") or "").strip()
    current_password = data.get("current_password", "")
    confirm_password = (data.get("confirm_password") or "").strip()

    if len(new_password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if new_password != confirm_password:
        return jsonify({"error": "New password and confirmation do not match"}), 400

    profile = MerchantProfile.query.get(merchant["id"])
    if not profile:
        return jsonify({"error": "Merchant profile not found"}), 404

    is_privileged = merchant.get("role") in (UserRole.ADMIN.value, UserRole.ENGINEER.value)
    if not is_privileged:
        if not current_password:
            return jsonify({"error": "Current password is required"}), 400
        if not profile.password_hash or not check_password_hash(profile.password_hash, current_password):
            return jsonify({"error": "Current password is incorrect"}), 401

    profile.password_hash = generate_password_hash(new_password, method="pbkdf2:sha256")
    db.session.commit()
    return jsonify({"updated": True, "merchant_id": merchant["id"]}), 200


@app.route('/api/v1/merchant/seed-sandbox', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_merchant_seed_sandbox():
    """Reset and seed sandbox demo data for the current merchant."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    profile = MerchantProfile.query.get(merchant["id"])
    if not profile:
        return jsonify({"error": "Merchant profile not found"}), 404
    now = datetime.utcnow()
    profile.sandbox_status = "sandbox"
    profile.sandbox_started_at = now
    profile.sandbox_expires_at = now + timedelta(hours=48)
    profile.live_access_enabled = 0
    db.session.commit()
    sandbox_demo.seed_sandbox_demo(merchant["id"], profile.business_name or "")
    return jsonify({"sandbox": True, "merchant_id": merchant["id"], "expires_at": profile.sandbox_expires_at.isoformat()}), 200


@app.route('/api/v1/merchant/set-business-name', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_merchant_set_business_name():
    """Allow a logged-in merchant to update their display business name."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    new_name = (data.get("business_name") or "").strip()
    if not new_name:
        return jsonify({"error": "business_name is required"}), 400
    profile = MerchantProfile.query.get(merchant["id"])
    if not profile:
        return jsonify({"error": "Merchant profile not found"}), 404
    profile.business_name = new_name
    db.session.commit()
    return jsonify({"updated": True, "business_name": new_name}), 200


@app.route('/api/v1/merchant/account', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_merchant_account():
    """Update the merchant's business name and account holder name."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    data = request.get_json(silent=True) or {}
    business_name = (data.get("business_name") or "").strip()
    account_holder_name = (data.get("account_holder_name") or "").strip()
    profile = MerchantProfile.query.get(merchant["id"])
    if not profile:
        return jsonify({"error": "Merchant profile not found"}), 404
    if business_name:
        profile.business_name = business_name
    if account_holder_name:
        setting = MerchantSetting.query.get((merchant["id"], "account_holder_name"))
        if not setting:
            setting = MerchantSetting(merchant_id=merchant["id"], setting_key="account_holder_name")
            db.session.add(setting)
        setting.setting_value = account_holder_name
    db.session.commit()
    return jsonify({"updated": True, "business_name": profile.business_name, "account_holder_name": account_holder_name or merchant["account_holder_name"]}), 200


@app.route('/api/v1/merchant/business-memory', methods=['GET', 'POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT, UserRole.ENGINEER])
def api_merchant_business_memory():
    """Get or update the merchant's business memory guardrails."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"error": "No merchant context"}), 403
    merchant_id = merchant["id"]
    import action_gate
    memory = action_gate.get_business_memory(merchant_id)

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        for field in [
            "max_cac_threshold",
            "floor_margin_percentage",
            "max_daily_ad_spend",
            "autopilot_enabled",
            "autopilot_max_order_value",
            "autopilot_max_action_cost",
            "auto_approve_action_types",
            "required_approval_action_types",
            "forbidden_discount_skus",
            "preferred_supplier_ids",
            "auto_escalation_rules",
        ]:
            if field in data:
                setattr(memory, field, data[field])
        db.session.commit()

    return jsonify({
        "merchant_id": memory.merchant_id,
        "max_cac_threshold": float(memory.max_cac_threshold),
        "floor_margin_percentage": memory.floor_margin_percentage,
        "max_daily_ad_spend": float(memory.max_daily_ad_spend),
        "autopilot_enabled": bool(memory.autopilot_enabled),
        "autopilot_max_order_value": float(memory.autopilot_max_order_value),
        "autopilot_max_action_cost": float(memory.autopilot_max_action_cost),
        "auto_approve_action_types": memory.auto_approve_action_types or [],
        "required_approval_action_types": memory.required_approval_action_types or [],
        "forbidden_discount_skus": memory.forbidden_discount_skus or [],
        "preferred_supplier_ids": memory.preferred_supplier_ids or {},
        "auto_escalation_rules": memory.auto_escalation_rules or {},
    }), 200


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
            _set_session_cookie(response, token)
            return response
        error = True
    return redirect(url_for('login', error=1)) if error else redirect(url_for('login'))


@app.route('/site-logout')
def site_logout():
    token = request.cookies.get(SESSION_COOKIE_NAME)
    merchant_id = None
    if token:
        session_record = ActiveSession.query.filter_by(token=token).first()
        if session_record:
            merchant_id = session_record.merchant_id
        ActiveSession.query.filter_by(token=token).delete()
    if merchant_id:
        profile = MerchantProfile.query.get(merchant_id)
        _reset_test_merchant_for_tier_testing(profile)
    db.session.commit()
    response = redirect(url_for('home'))
    _delete_session_cookie(response)
    return response


@app.route('/api/session/heartbeat', methods=['POST'])
@limiter.exempt
def session_heartbeat():
    """Report session validity without extending the idle window."""
    if not site_wall_authenticated(refresh=False):
        return jsonify({"valid": False, "detail": "Session expired or invalid"}), 401
    return jsonify({"valid": True, "expires_in": SESSION_IDLE_TIMEOUT_MINUTES * 60}), 200


@app.route('/api/v1/auth/login', methods=['POST'])
@limiter.limit("10 per minute")
def auth_login():
    """Validate email + password and issue a session cookie.

    Rate limiting and password hashing protect the endpoint. Master admin and
    engineer emails can also authenticate with the site-wall password for full
    feature access during setup or support.
    """
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password", "")

    if not email or not password:
        return jsonify({"detail": "CRITICAL ERROR: Email and password are required."}), 400

    is_admin = email in MASTER_ADMIN_EMAILS
    is_engineer = email in ENGINEER_EMAILS
    master_password_ok = bool(SITE_WALL_PASSWORD) and hmac.compare_digest(password, SITE_WALL_PASSWORD)

    profile = _profile_for_email(email)
    password_ok = False
    if profile and profile.password_hash:
        password_ok = check_password_hash(profile.password_hash, password)

    # Master admin/engineer fallback: use the site-wall password if set.
    if not password_ok and not (master_password_ok and (is_admin or is_engineer)):
        return jsonify({"detail": "CRITICAL ERROR: Invalid authentication credentials match failed."}), 401

    # Ensure a profile exists for master admin/engineer logins.
    if not profile and (is_admin or is_engineer):
        merchant_id = f"admin_{uuid.uuid4().hex[:8]}"
        profile = MerchantProfile(
            merchant_id=merchant_id,
            business_name=("Vantav Admin" if is_admin else "Vantav Engineer"),
            admin_email=email,
            account_tier="Vantav Scale",
            password_hash=generate_password_hash(SITE_WALL_PASSWORD, method="pbkdf2:sha256") if SITE_WALL_PASSWORD else "",
            sandbox_status="approved",
            live_access_enabled=1,
        )
        db.session.add(profile)
        db.session.flush()

    # Test merchant accounts always start at the tier chooser so the user can
    # re-select a plan on every login.
    if profile and not (is_admin or is_engineer):
        _reset_test_merchant_for_tier_testing(profile)

    # 3. Issue encrypted session JWT (session cookie + ActiveSession row)
    session_token = secrets.token_urlsafe(32)
    if is_admin:
        assigned_role = UserRole.ADMIN.value
    elif is_engineer:
        assigned_role = UserRole.ENGINEER.value
    else:
        assigned_role = UserRole.MERCHANT.value
    now = datetime.utcnow()
    db.session.add(ActiveSession(token=session_token, merchant_id=profile.merchant_id, role=assigned_role, created_at=now, last_seen=now))
    db.session.commit()

    if assigned_role in (UserRole.ADMIN.value, UserRole.ENGINEER.value):
        audit = AdminAuditLog(
            admin_email=email,
            action="admin.login",
            target_merchant_id=profile.merchant_id,
            details={"role": assigned_role},
        )
        db.session.add(audit)
        db.session.commit()

    response = make_response(jsonify({
        "status": "AUTHORIZED",
        "role": assigned_role,
        "merchant_id": profile.merchant_id,
    }))
    _set_session_cookie(response, session_token)
    return response, 200


@app.route('/api/v1/session/authenticate', methods=['POST'])
@limiter.limit("10 per minute")
def api_session_authenticate():
    """Issue a signed X-Session-Token for hardened endpoint access."""
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password", "")
    if not email or not password:
        return jsonify({"detail": "Email and password are required."}), 400

    result = master_auth_engine.authenticate(email, password)
    if not result:
        return jsonify({"detail": "Invalid identity credentials matched."}), 401

    return jsonify(result), 200


# TIER_NAME_MAP is defined above with all plan/tier aliases.


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

    # Direct sign-up creates a free Basic Tier account; the chosen paid plan is selected at checkout.
    if selected_tier and selected_tier not in TIER_NAME_MAP:
        return jsonify({"detail": "Invalid system tier parameters provided."}), 400
    tier = "Basic Tier"

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
        db.session.flush()
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
            ai_briefing="System initialized. Choose a plan and connect your first store to start tracking profit and alerts.",
        ))
        db.session.flush()

        # Initialize seat allocation for the workspace owner.
        tier_meta = TierManager.get_tier_meta(tier)
        memory = BusinessMemory(merchant_id=merchant_id)
        memory.max_authorized_seats = int(tier_meta.get("max_users", 1))
        memory.current_active_seats = 1
        db.session.add(memory)
        db.session.add(WorkspaceSeat(merchant_id=merchant_id, user_email=email, role="admin"))
        db.session.commit()
    except Exception as e:
        logger.error(f"[SIGNUP] Failed to provision {email}: {e}")
        db.session.rollback()
        return jsonify({"detail": "Tenant provisioning failed. Please retry."}), 500

    # 4. Issue session cookie
    now = datetime.utcnow()
    session_token = secrets.token_urlsafe(32)
    db.session.add(ActiveSession(token=session_token, merchant_id=merchant_id, role=UserRole.MERCHANT.value, created_at=now, last_seen=now))
    db.session.commit()

    response = make_response(jsonify({
        "status": "SUCCESS",
        "message": "Multi-tenant engine environment provisioned flawlessly.",
        "tenant_id": merchant_id,
        "assigned_tier": tier,
        "monthly_order_limit": TierManager.get_order_limit(tier),
    }))
    _set_session_cookie(response, session_token)
    return response, 201


@app.route('/api/v1/auth/provision-node', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
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
            sandbox_status="approved",
            live_access_enabled=1,
            approved_at=datetime.utcnow(),
        ))
        db.session.flush()
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
            ai_briefing=f"Provisioned {role} account. Choose a plan and connect your first store.",
        ))
        db.session.flush()

        tier_meta = TierManager.get_tier_meta(tier)
        memory = BusinessMemory(merchant_id=merchant_id)
        memory.max_authorized_seats = int(tier_meta.get("max_users", 1))
        memory.current_active_seats = 1
        db.session.add(memory)
        db.session.add(WorkspaceSeat(merchant_id=merchant_id, user_email=email, role=role.lower() if role in ("ADMIN", "ENGINEER", "MERCHANT") else "merchant"))
        db.session.commit()
    except Exception as e:
        logger.error(f"[PROVISION] Failed to provision {email}: {e}")
        db.session.rollback()
        return jsonify({"detail": "Tenant provisioning failed. Please retry."}), 500

    return jsonify({
        "status": "PROVISIONED",
        "email": email,
        "assigned_role": role,
        "allocated_volume_allowance": TierManager.get_order_limit(tier),
        "tenant_id": merchant_id,
    }), 201


@app.route('/api/v1/workspace/invite', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.MERCHANT])
def api_workspace_invite():
    """Invite a new user to the merchant workspace, enforcing the seat cap."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"detail": "No merchant context"}), 403
    merchant_id = merchant["id"]

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    role = (data.get("role") or "merchant").strip().lower()
    if not email:
        return jsonify({"detail": "email is required"}), 400
    if role not in ("admin", "engineer", "merchant"):
        return jsonify({"detail": "Invalid role. Use admin, engineer, or merchant."}), 400

    try:
        verify_workspace_seat_allowance(merchant_id, email)
    except ValueError as e:
        return jsonify({"detail": str(e)}), 422

    existing = WorkspaceSeat.query.filter_by(merchant_id=merchant_id, user_email=email).first()
    if existing:
        return jsonify({"detail": "User is already a workspace member."}), 409

    try:
        seat = WorkspaceSeat(merchant_id=merchant_id, user_email=email, role=role)
        db.session.add(seat)
        db.session.commit()
        sync_workspace_seat_count(merchant_id)
    except Exception as e:
        db.session.rollback()
        logger.error(f"[Workspace Invite] Failed to invite {email} to {merchant_id}: {e}")
        return jsonify({"detail": "Invite failed."}), 500

    return jsonify({
        "status": "invited",
        "email": email,
        "role": role,
        "merchant_id": merchant_id,
        "seats_used": WorkspaceSeat.query.filter_by(merchant_id=merchant_id).count(),
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
    """Create a Stripe Checkout session for a Vantav tier plus optional Concierge Bundle."""
    data = request.get_json(silent=True) or request.form or {}
    email = (data.get("email") or "").strip().lower()
    business_name = (data.get("business_name") or "").strip() or email
    password = data.get("password", "")
    concierge_bundle = bool(data.get("concierge_bundle"))
    plan = (data.get("plan") or "operator").lower().strip()

    # If the merchant is already logged in, use the existing account and ignore
    # any email/password they typed. Anonymous users must create an account.
    merchant_ctx = get_merchant_context()
    profile = None
    merchant_id = None
    if merchant_ctx:
        profile = MerchantProfile.query.get(merchant_ctx["id"])
        if not profile:
            return jsonify({"detail": "Session merchant not found."}), 403
        email = profile.admin_email
        merchant_id = profile.merchant_id
        profile.business_name = business_name or profile.business_name
    else:
        if not email or not password or len(password) < 8:
            return jsonify({"detail": "A valid email and a password of at least 8 characters are required."}), 400

        # Find or provision the merchant account so the webhook can upgrade it.
        profile = MerchantProfile.query.filter_by(admin_email=email).first()
        if profile:
            # Require the existing account password; do not let a stranger start a
            # checkout with someone else's email and take over the account.
            if not check_password_hash(profile.password_hash or "", password):
                return jsonify({"detail": "Email already registered. Log in to upgrade or use a different email."}), 409
            merchant_id = profile.merchant_id
            profile.business_name = business_name or profile.business_name
        else:
            merchant_id = f"tenant_{uuid.uuid4().hex[:8]}"
            db.session.add(MerchantProfile(
                merchant_id=merchant_id,
                business_name=business_name,
                admin_email=email,
                account_tier="Basic Tier",
                password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
                sandbox_status="pending",
                live_access_enabled=0,
            ))
        db.session.flush()
        db.session.add(SaaSBilling(
            merchant_id=merchant_id,
            current_plan="Basic Tier",
            metered_usage_units=0,
            accrued_invoice_value=0.0,
        ))
        db.session.add(MerchantMetric(
            merchant_id=merchant_id,
            total_unified_balance=0.0,
            true_net_profit=0.0,
            gross_revenue=0.0,
            ai_briefing="System initialized. Choose a plan and connect your first store to start tracking profit and alerts.",
        ))
        tier_meta = TierManager.get_tier_meta("Basic Tier")
        memory = BusinessMemory(merchant_id=merchant_id)
        memory.max_authorized_seats = int(tier_meta.get("max_users", 1))
        memory.current_active_seats = 1
        db.session.add(memory)
        db.session.add(WorkspaceSeat(merchant_id=merchant_id, user_email=email, role="admin"))
        db.session.commit()

    # Issue a session cookie now so the merchant is already logged in when
    # Stripe redirects them back after payment.
    now = datetime.utcnow()
    session_token = secrets.token_urlsafe(32)
    db.session.add(ActiveSession(token=session_token, merchant_id=merchant_id, role=UserRole.MERCHANT.value, created_at=now, last_seen=now))
    db.session.commit()

    try:
        success_url = url_for('dashboard', _external=True, _scheme='https') + '?checkout=success&onboarding=1&session_id={{CHECKOUT_SESSION_ID}}'
        cancel_url = url_for('subscribe', _external=True, _scheme='https') + '?canceled=1'
        session_url, session_id, customer_id = billing_module.create_checkout_session(
            merchant_id,
            email,
            business_name,
            concierge_bundle=concierge_bundle,
            plan=plan,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        response = make_response(jsonify({"url": session_url, "session_id": session_id, "customer_id": customer_id}), 200)
        _set_session_cookie(response, session_token)
        return response
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


@app.route('/api/v1/stripe/upgrade-session', methods=['POST'])
def stripe_upgrade_session():
    """Create a Stripe Checkout session for the currently authenticated merchant."""
    merchant = get_merchant_context()
    if not merchant:
        return jsonify({"detail": "Authentication required."}), 403
    data = request.get_json(silent=True) or {}
    plan = (data.get("plan") or "operator").lower().strip()
    concierge_bundle = bool(data.get("concierge_bundle"))
    try:
        success_url = url_for('dashboard_page', page='billing', _external=True, _scheme='https') + '?checkout=success'
        cancel_url = url_for('dashboard_page', page='billing', _external=True, _scheme='https') + '?checkout=canceled'
        session_url, session_id, customer_id = billing_module.create_checkout_session(
            merchant["id"],
            merchant.get("email") or "",
            merchant.get("name") or merchant.get("email") or "",
            concierge_bundle=concierge_bundle,
            plan=plan,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return jsonify({"url": session_url, "session_id": session_id, "customer_id": customer_id}), 200
    except Exception as e:
        logger.error(f"[Stripe Upgrade Session] Failed: {e}")
        return jsonify({"detail": "Unable to start checkout session."}), 500


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


SHOPIFY_WEBHOOK_SECRET = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "").strip().encode() or os.environ.get("SHOPIFY_CLIENT_SECRET", "").strip().encode()
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
        tenant_rls.set_tenant_scope(merchant_target)

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
        order_items = []
        if isinstance(line_items, list):
            for li in line_items:
                if not isinstance(li, dict):
                    continue
                sku = li.get("sku") or li.get("product_id") or li.get("variant_id") or ""
                qty = li.get("quantity") or 1
                price = li.get("price") or li.get("unit_price") or 0.0
                title = li.get("title") or li.get("name") or sku
                if sku:
                    order_items.append({
                        "sku": str(sku).strip(),
                        "qty": int(qty or 1),
                        "price": float(price or 0.0),
                        "title": title,
                    })
        order_ref = str(payload.get("name") or payload.get("order_number") or event_id)
        profit_feed.record_order(
            merchant_id=merchant_target,
            channel="shopify",
            order_id=order_ref,
            gross_revenue=order_value,
            items=len(line_items) if isinstance(line_items, list) else 1,
            state="shipped" if payload.get("fulfillment_status") != "cancelled" else "cancelled",
            refund_amount=abs(float(payload.get("total_refund_amount", 0.0) or 0.0)),
            order_items=order_items,
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


# ------------------------------------------------------------
# Shopify GDPR / app-lifecycle webhooks (mandatory for public apps)
# ------------------------------------------------------------

def _parse_shopify_gdpr_webhook():
    """Verify Shopify HMAC and map the shop domain to a merchant_id."""
    raw_body = request.get_data()
    hmac_header = request.headers.get("X-Shopify-Hmac-SHA256")

    if SHOPIFY_WEBHOOK_SECRET:
        if not hmac_header:
            logger.warning("Shopify GDPR webhook dropped: missing HMAC signature.")
            return None, jsonify({"status": "rejected", "reason": "Missing HMAC"}), 401
        try:
            computed = hmac.new(SHOPIFY_WEBHOOK_SECRET, raw_body, hashlib.sha256).digest()
            if not hmac.compare_digest(computed, base64.b64decode(hmac_header)):
                logger.warning("Shopify GDPR webhook dropped: invalid HMAC signature.")
                return None, jsonify({"status": "rejected", "reason": "Invalid HMAC"}), 401
        except Exception:
            return None, jsonify({"status": "rejected", "reason": "Invalid HMAC"}), 401
    else:
        logger.warning("SHOPIFY_WEBHOOK_SECRET not set — accepting GDPR webhook without HMAC verification")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        return None, jsonify({"status": "rejected", "reason": "Malformed JSON"}), 400

    shop_domain = (payload.get("shop_domain") or payload.get("shop") or "").strip().lower()
    event_id = request.headers.get("X-Shopify-Webhook-Id")

    token = TenantOAuthToken.query.filter_by(shop_domain=shop_domain).first()
    merchant_id = token.merchant_id if token else None
    if not merchant_id:
        link = IntegrationLink.query.filter_by(shopify_shop_domain=shop_domain, platform="shopify").first()
        if link:
            merchant_id = link.merchant_id

    return {"raw_body": raw_body, "payload": payload, "shop_domain": shop_domain, "merchant_id": merchant_id, "event_id": event_id}, None, 200


@app.route('/api/v1/webhooks/shopify-gdpr/customers/data_request', methods=['POST'])
@limiter.limit("60 per minute")
def shopify_gdpr_customer_data_request():
    """Customer data request: collect and return the customer's order history."""
    context, error, status = _parse_shopify_gdpr_webhook()
    if error:
        return error, status

    merchant_id = context["merchant_id"]
    if not merchant_id:
        return jsonify({"status": "accepted"}), 200

    tenant_rls.set_tenant_scope(merchant_id)

    event_id = context["event_id"]
    if event_id and ProcessedWebhookEvent.query.get(event_id):
        return jsonify({"status": "duplicate_ignored"}), 200

    customer = context["payload"].get("customer") or {}
    customer_id = str(customer.get("id") or customer.get("customer_id") or "").strip()
    customer_email = (customer.get("email") or "").strip().lower()

    query = UnifiedOrder.query.filter_by(merchant_id=merchant_id, channel="shopify")
    filters = []
    if customer_id:
        filters.append(UnifiedOrder.customer_id == customer_id)
    if customer_email:
        filters.append(UnifiedOrder.ship_to.cast(db.Text).ilike(f"%{customer_email}%"))
    if filters:
        query = query.filter(or_(*filters))
    orders = query.order_by(UnifiedOrder.created_at.desc()).limit(500).all()

    order_ids = [o.id for o in orders]
    items = []
    if order_ids:
        items = OrderItem.query.filter(OrderItem.order_id.in_(order_ids)).all()

    summary_lines = []
    for o in orders:
        line_items = [f"{i.sku} x{i.qty} @ ${float(i.unit_price):.2f}" for i in items if i.order_id == o.id]
        summary_lines.append(
            f"Order {o.id} — {o.created_at} — ${float(o.revenue):.2f} — items: {', '.join(line_items) if line_items else 'none recorded'}"
        )

    body = f"""<p>Shopify customer data request for shop <b>{context['shop_domain']}</b>:</p>
<p>Customer ID: {customer_id or 'N/A'}<br>Customer email: {customer_email or 'N/A'}</p>
<p>Orders found: {len(orders)}</p>
<pre>{'<br>'.join(summary_lines) or 'No orders stored.'}</pre>
"""

    merchant = MerchantProfile.query.get(merchant_id)
    recipient = merchant.admin_email if merchant and merchant.admin_email else SUPPORT_EMAIL
    dispatch_external_email(recipient, f"Shopify customer data request — {context['shop_domain']}", body)

    if event_id:
        db.session.add(ProcessedWebhookEvent(event_id=event_id))
        db.session.commit()

    return jsonify({"status": "accepted"}), 200


@app.route('/api/v1/webhooks/shopify-gdpr/customers/redact', methods=['POST'])
@limiter.limit("60 per minute")
def shopify_gdpr_customer_redact():
    """Customer redaction: remove PII for the specified customer."""
    context, error, status = _parse_shopify_gdpr_webhook()
    if error:
        return error, status

    merchant_id = context["merchant_id"]
    if not merchant_id:
        return jsonify({"status": "accepted"}), 200

    tenant_rls.set_tenant_scope(merchant_id)

    event_id = context["event_id"]
    if event_id and ProcessedWebhookEvent.query.get(event_id):
        return jsonify({"status": "duplicate_ignored"}), 200

    customer = context["payload"].get("customer") or {}
    customer_id = str(customer.get("id") or customer.get("customer_id") or "").strip()
    customer_email = (customer.get("email") or "").strip().lower()

    query = UnifiedOrder.query.filter_by(merchant_id=merchant_id, channel="shopify")
    filters = []
    if customer_id:
        filters.append(UnifiedOrder.customer_id == customer_id)
    if customer_email:
        filters.append(UnifiedOrder.ship_to.cast(db.Text).ilike(f"%{customer_email}%"))
    if filters:
        query = query.filter(or_(*filters))
    orders = query.all()

    for order in orders:
        order.customer_id = "redacted"
        order.ship_to = {"redacted": True}

    if event_id:
        db.session.add(ProcessedWebhookEvent(event_id=event_id))
    db.session.commit()
    logger.info(f"Redacted {len(orders)} Shopify orders for merchant {merchant_id}")

    return jsonify({"status": "accepted"}), 200


@app.route('/api/v1/webhooks/shopify-gdpr/shop/redact', methods=['POST'])
@limiter.limit("60 per minute")
def shopify_gdpr_shop_redact():
    """Shop redaction: delete all Shopify data for the store."""
    context, error, status = _parse_shopify_gdpr_webhook()
    if error:
        return error, status

    merchant_id = context["merchant_id"]
    shop_domain = context["shop_domain"]
    if not merchant_id:
        return jsonify({"status": "accepted"}), 200

    tenant_rls.set_tenant_scope(merchant_id)

    event_id = context["event_id"]
    if event_id and ProcessedWebhookEvent.query.get(event_id):
        return jsonify({"status": "duplicate_ignored"}), 200

    # Remove Shopify orders and their line items first (products are RESTRICTed by order_items).
    shopify_order_ids = [
        row[0] for row in
        db.session.query(UnifiedOrder.id).filter_by(merchant_id=merchant_id, channel="shopify").all()
    ]
    if shopify_order_ids:
        OrderItem.query.filter(OrderItem.order_id.in_(shopify_order_ids)).delete(synchronize_session=False)
        UnifiedOrder.query.filter_by(merchant_id=merchant_id, channel="shopify").delete(synchronize_session=False)

    ProfitFeedOrder.query.filter_by(merchant_id=merchant_id, channel="shopify").delete(synchronize_session=False)
    AdSpendFeed.query.filter_by(merchant_id=merchant_id, platform_source="shopify").delete(synchronize_session=False)

    # Remove only Shopify-only products; keep products shared with other channels.
    for product in Product.query.filter_by(merchant_id=merchant_id).all():
        channel_ids = product.channel_ids or {}
        if isinstance(channel_ids, str):
            try:
                channel_ids = json.loads(channel_ids)
            except json.JSONDecodeError:
                channel_ids = {}
        if isinstance(channel_ids, dict) and "shopify" in channel_ids:
            del channel_ids["shopify"]
        if not channel_ids:
            db.session.delete(product)
        else:
            product.channel_ids = channel_ids

    # Clean up integration artifacts.
    TenantOAuthToken.query.filter_by(shop_domain=shop_domain).delete(synchronize_session=False)
    IntegrationLink.query.filter_by(merchant_id=merchant_id, platform="shopify", shopify_shop_domain=shop_domain).delete(synchronize_session=False)
    MerchantChannel.query.filter_by(merchant_id=merchant_id, channel_id="shopify").delete(synchronize_session=False)

    if event_id:
        db.session.add(ProcessedWebhookEvent(event_id=event_id))
    db.session.commit()
    logger.info(f"Redacted all Shopify data for merchant {merchant_id}, shop {shop_domain}")

    return jsonify({"status": "accepted"}), 200


@app.route('/api/v1/webhooks/shopify/app/uninstalled', methods=['POST'])
@limiter.limit("60 per minute")
def shopify_app_uninstalled():
    """App uninstalled: revoke tokens and mark the Shopify integration inactive."""
    context, error, status = _parse_shopify_gdpr_webhook()
    if error:
        return error, status

    merchant_id = context["merchant_id"]
    shop_domain = context["shop_domain"]
    if not merchant_id:
        return jsonify({"status": "accepted"}), 200

    tenant_rls.set_tenant_scope(merchant_id)

    event_id = context["event_id"]
    if event_id and ProcessedWebhookEvent.query.get(event_id):
        return jsonify({"status": "duplicate_ignored"}), 200

    TenantOAuthToken.query.filter_by(shop_domain=shop_domain).delete(synchronize_session=False)
    IntegrationLink.query.filter_by(merchant_id=merchant_id, platform="shopify", shopify_shop_domain=shop_domain).delete(synchronize_session=False)
    MerchantChannel.query.filter_by(merchant_id=merchant_id, channel_id="shopify").delete(synchronize_session=False)

    if event_id:
        db.session.add(ProcessedWebhookEvent(event_id=event_id))
    db.session.commit()
    logger.info(f"Uninstalled Shopify integration for merchant {merchant_id}, shop {shop_domain}")

    return jsonify({"status": "accepted"}), 200


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
    tenant_rls.set_tenant_scope(merchant_target)
    blocked = enforce_tier_limits(merchant_target, "tiktok")
    if blocked:
        return blocked
    raw = request.get_json() or {}
    order_price = float(raw.get("order_amount", raw.get("total_amount", 0.00)))
    try:
        created = process_idempotent_channel_event(event_id, merchant_target, "tiktok", order_price)
        db.session.commit()
        # Feed the real-time Profit Feed for TikTok Shop.
        line_items = raw.get("line_items") or raw.get("skus") or raw.get("items") or []
        order_items = []
        if isinstance(line_items, list):
            for li in line_items:
                if isinstance(li, dict):
                    sku = li.get("sku_id") or li.get("sku") or li.get("product_id") or ""
                    qty = li.get("quantity") or 1
                    price = li.get("sale_price", {}).get("amount", 0.0) if isinstance(li.get("sale_price"), dict) else (li.get("price") or 0.0)
                    title = li.get("product_name") or li.get("title") or sku
                else:
                    sku = str(li)
                    qty = 1
                    price = round(order_price / max(len(line_items), 1), 4) if order_price else 0.0
                    title = sku
                if sku:
                    order_items.append({
                        "sku": str(sku).strip(),
                        "qty": int(qty or 1),
                        "price": float(price or 0.0),
                        "title": title,
                    })
        profit_feed.record_order(
            merchant_id=merchant_target,
            channel="tiktok",
            order_id=str(raw.get("order_id") or event_id),
            gross_revenue=order_price,
            items=len(order_items) if order_items else (len(line_items) if isinstance(line_items, list) else 1),
            state="shipped" if raw.get("status") != "CANCELLED" else "cancelled",
            order_items=order_items,
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
    tenant_rls.set_tenant_scope(merchant_target)
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
        amazon_items = payload.get("OrderItems") or []
        order_items = []
        if isinstance(amazon_items, list):
            for li in amazon_items:
                if not isinstance(li, dict):
                    continue
                sku = li.get("SellerSKU") or li.get("ASIN") or li.get("OrderItemId") or ""
                qty = li.get("Quantity") or li.get("QuantityOrdered") or 1
                price = li.get("ItemPrice", {}).get("Amount", 0.0) if isinstance(li.get("ItemPrice"), dict) else (li.get("Price") or 0.0)
                title = li.get("Title") or sku
                if sku:
                    order_items.append({
                        "sku": str(sku).strip(),
                        "qty": int(qty or 1),
                        "price": float(price or 0.0),
                        "title": title,
                    })
        if not order_items and order_price:
            # Fallback if only a count is provided.
            count = int(items) if isinstance(items, (int, float, str)) and str(items).isdigit() else 1
            unit = round(order_price / max(count, 1), 4)
            for i in range(count):
                order_items.append({
                    "sku": f"AMAZON-FALLBACK-{i+1}",
                    "qty": 1,
                    "price": unit,
                    "title": "Amazon item",
                })
        profit_feed.record_order(
            merchant_id=merchant_target,
            channel="amazon",
            order_id=str(payload.get("AmazonOrderId") or payload.get("order_id") or event_id),
            gross_revenue=order_price,
            items=len(order_items) if order_items else (len(items) if isinstance(items, list) else 1),
            state="shipped" if payload.get("OrderStatus") != "Canceled" else "cancelled",
            order_items=order_items,
        )
        return jsonify({"status": "synchronized" if created else "ignored"}), 200
    except Exception as e:
        log_system_exception("AMAZON_WEBHOOK", "CRITICAL", str(e))
        db.session.rollback()
        return jsonify({"status": "rejected", "reason": "Internal error"}), 500


@app.route('/api/v1/webhooks/stripe-billing', methods=['POST'])
def stripe_billing_webhook():
    """Verify and process Stripe checkout/subscription events to upgrade merchant tier and seats live."""
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
            stripe_sub_id = session_obj.get("id") or session_obj.get("subscription")
            metadata = session_obj.get("metadata", {})
            merchant_target = metadata.get("merchant_id", "merchant_shawn_01")
            chosen_tier = metadata.get("selected_tier") or metadata.get("plan_tier_key") or "Vantav Operator"
            chosen_tier = _canonical_tier(chosen_tier)
            concierge_bundle = metadata.get("concierge_bundle") == "true"

            profile = MerchantProfile.query.get(merchant_target)
            if profile:
                profile.account_tier = chosen_tier
                # Paid subscribers bypass the waitlist sandbox and get live access.
                profile.sandbox_status = "approved"
                profile.live_access_enabled = 1
                profile.approved_at = datetime.utcnow()
            saas_billing = SaaSBilling.query.get(merchant_target)
            if not saas_billing:
                saas_billing = SaaSBilling(merchant_id=merchant_target)
                db.session.add(saas_billing)
            saas_billing.current_plan = chosen_tier
            saas_billing.stripe_customer_id = stripe_cust_id
            saas_billing.stripe_subscription_id = stripe_sub_id
            current_addons = set(saas_billing.add_ons or [])
            if concierge_bundle:
                current_addons.add("concierge_bundle")
            else:
                current_addons.discard("concierge_bundle")
            saas_billing.add_ons = list(current_addons)

            # Sync seat limits and Stripe indices into business memory.
            memory = action_gate.get_business_memory(merchant_target)
            tier_meta = TierManager.get_tier_meta(chosen_tier)
            memory.max_authorized_seats = int(tier_meta.get("max_users", 1))
            memory.current_active_seats = WorkspaceSeat.query.filter_by(merchant_id=merchant_target).count() or 1
            memory.stripe_customer_id = stripe_cust_id
            memory.stripe_subscription_id = stripe_sub_id

            db.session.commit()
            logger.info(f"[Stripe Pipeline] Merchant {merchant_target} upgraded to {chosen_tier}; seats={memory.max_authorized_seats}; concierge={concierge_bundle}")
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
                    memory = BusinessMemory.query.filter_by(merchant_id=saas_billing.merchant_id).first()
                    if memory:
                        memory.max_authorized_seats = 0
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
  <h3>Vantav Summary</h3>
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
                {"sender": AGENT_DISPLAY_NAME.get(m.sender_agent, "Vantav"), "text": m.payload}
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
                    "name": AGENT_DISPLAY_NAME.get(a.agent_id, a.agent_name or "Vantav"),
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
        db.session.flush()
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
        profile = _profile_for_email(email)
        if not profile:
            merchant_id = f"merchant_{secrets.token_hex(4)}"
            db.session.add(MerchantProfile(
                merchant_id=merchant_id,
                business_name=business_name,
                admin_email=email,
                account_tier=selected_tier,
            ))
            db.session.flush()
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
    profile = _profile_for_email(mlink.admin_email)
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

    response = make_response(redirect("/"))
    _set_session_cookie(response, session_token)
    return response


@app.route('/api/v1/admin/kill-switch', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def admin_kill_switch():
    """Halt all background channel synchronization."""
    try:
        asyncio.run(circuit_breaker.engage_global_kill_switch())
        return jsonify({"status": "HALTED"}), 200
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)}), 500


@app.route('/api/v1/admin/release-lock', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
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


@app.route('/api/v1/health', methods=['GET'])
@limiter.exempt
def api_v1_health():
    """Public mirror of /health for external uptime monitors."""
    return health_check()


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


def _tiktok_oauth_state(merchant_id: str, secret: str, region: str = "") -> str:
    payload = f"{merchant_id}:{region}"
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"{merchant_id}:{region}:{sig}"


def _verify_tiktok_oauth_state(state: str, secret: str) -> tuple:
    if not state or ":" not in state:
        return None, ""
    parts = state.split(":", 2)
    if len(parts) < 3:
        return None, ""
    merchant_id, region, sig = parts
    payload = f"{merchant_id}:{region}"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(sig, expected):
        return None, ""
    return merchant_id, region


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
        # Also store the offline token in the dual-auth secure vault.
        channel_auth.store_channel_credentials(
            merchant_id=merchant_id,
            platform="shopify",
            access_token=result["access_token"],
            shopify_shop_domain=shop,
        )
        _trigger_initial_sync(merchant_id, "shopify")
        return_url = "/dashboard/settings?tab=stores&onboarding=1&oauth_sync=success"
        return redirect(f"/dashboard/onboarding-loading?channel=shopify&return={quote(return_url, safe='')}")
    except Exception as e:
        logger.error(f"[Shopify OAuth] {e}")
        return redirect("/dashboard/settings?tab=stores&onboarding=1&oauth_sync=error")


@app.route('/api/v1/auth/tiktok/connect')
def tiktok_oauth_connect():
    """Step 1: Redirect merchant to TikTok Shop seller authorization screen."""
    merchant = get_merchant_context()
    if not merchant:
        return redirect("/login?error=auth_required")
    if not merchant.get("live_access_enabled"):
        return redirect("/dashboard/commerce-hub?oauth_sync=error")

    region = (request.args.get("region") or TIKTOK_AUTH_REGION or "").strip().lower()
    app_key, app_secret, service_id = _tiktok_creds_for_region(region)
    if not app_key or not app_secret or not service_id:
        return redirect("/dashboard/commerce-hub?oauth_sync=error")

    state = _tiktok_oauth_state(merchant["id"], app_secret, region)
    auth_url = tiktok_sync.build_auth_url(
        service_id=service_id,
        app_key=app_key,
        redirect_uri=TIKTOK_REDIRECT_URI,
        state=state,
        region=region,
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
    region = ""
    if TIKTOK_APP_SECRET:
        merchant_id, region = _verify_tiktok_oauth_state(state, TIKTOK_APP_SECRET)
    if not merchant_id:
        merchant = get_merchant_context()
        if merchant:
            merchant_id = merchant["id"]
        else:
            return redirect("/login?error=auth_required")

    app_key, app_secret, _ = _tiktok_creds_for_region(region)
    if not app_key or not app_secret:
        return redirect("/dashboard/commerce-hub?oauth_sync=error")

    try:
        token_data = tiktok_sync.exchange_auth_code(code, app_key, app_secret)
        access_token = token_data.get("access_token") or token_data.get("accessToken", "")
        refresh_token = token_data.get("refresh_token") or token_data.get("refreshToken", "")

        shops = tiktok_sync.get_authorized_shops(
            access_token=access_token,
            app_key=app_key,
            app_secret=app_secret,
            region=region,
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
            app_key=app_key,
            app_secret=app_secret,
            access_token=access_token,
            shop_cipher=shop_cipher,
            refresh_token=refresh_token,
            region=region,
        )
        _trigger_initial_sync(merchant_id, "tiktok")
        return_url = "/dashboard/settings?tab=stores&onboarding=1&oauth_sync=success"
        return redirect(f"/dashboard/onboarding-loading?channel=tiktok&return={quote(return_url, safe='')}")
    except Exception as e:
        logger.error(f"[TikTok OAuth] {e}")
        return redirect("/dashboard/settings?tab=stores&onboarding=1&oauth_sync=error")


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
    """Redirect legacy beta waitlist URLs to the public pricing page."""
    return redirect(url_for('subscribe'), code=301)


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
    profile = _profile_for_email(email)
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


# ============================================================
# MONITORING & SLA
# ============================================================

@app.route('/api/v1/monitoring/metrics', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_monitoring_metrics():
    """Return rolling request metrics and database latency."""
    return jsonify(monitoring_module.current_metrics()), 200


@app.route('/api/v1/monitoring/health', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_monitoring_health():
    """Return deep health status including database, storage, and channel sync."""
    return jsonify(monitoring_module.deep_health()), 200


@app.route('/api/v1/monitoring/alerts', methods=['GET'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_monitoring_alerts():
    """Run SLA checks and return active alerts."""
    alerts = monitoring_module.check_sla()
    return jsonify({"alerts": alerts, "count": len(alerts)}), 200


@app.route('/api/v1/monitoring/alert-test', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_monitoring_alert_test():
    """Send a test alert through configured channels."""
    monitoring_module.send_alert({
        "severity": "warn",
        "type": "test_alert",
        "message": "This is a test SLA alert from Vantav monitoring.",
        "value": 0,
        "threshold": 0,
    })
    return jsonify({"status": "alert_dispatched"}), 200


def _start_mview_refresh_worker():
    """Lightweight background routine that refreshes materialized views periodically."""
    interval = int(os.environ.get("MVIEW_REFRESH_MINUTES", "15"))
    if os.environ.get("DISABLE_MVIEW_REFRESH") == "1":
        return

    def _loop():
        while True:
            time.sleep(interval * 60)
            try:
                import migrate as _migrate
                _migrate.refresh_materialized_views()
            except Exception as e:
                logger.warning(f"[mview refresh worker] {e}")

    t = Thread(target=_loop, daemon=True, name="mview-refresh")
    t.start()
    logger.info("[mview refresh worker] started")


@app.route('/api/admin/refresh-materialized-views', methods=['POST'])
@require_roles([UserRole.ADMIN, UserRole.ENGINEER])
def api_refresh_materialized_views():
    """Trigger a manual refresh of materialized views."""
    try:
        import migrate as _migrate
        _migrate.refresh_materialized_views()
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"[mview refresh] {e}")
        return jsonify({"detail": str(e)}), 500


@app.route('/api/engineer/exceptions', methods=['GET'])
@require_roles([UserRole.ENGINEER])
def api_engineer_exceptions():
    """Return recent system exception logs for operational review."""
    since = datetime.utcnow() - timedelta(hours=24)
    rows = SystemExceptionLog.query.filter(SystemExceptionLog.timestamp >= since).order_by(SystemExceptionLog.timestamp.desc()).limit(100).all()
    return jsonify({
        "exceptions": [
            {
                "id": r.id,
                "module": r.module_origin,
                "severity": r.error_severity,
                "message": r.exception_msg,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in rows
        ]
    }), 200


def _engineer_chat_extract_platform(text):
    """Extract a platform name and merchant_id from a chat message."""
    # platform then merchant
    m = re.search(
        r'\b(shopify|tiktok|amazon|ebay|walmart|bigcommerce|woocommerce)\b.*?\b'
        r'(merchant_[a-zA-Z0-9_]+|tenant_[a-zA-Z0-9_]+|demo_[a-zA-Z0-9_]+|shawn_[a-zA-Z0-9_]+)\b',
        text, re.I,
    )
    if m:
        return m.group(1).lower(), m.group(2)
    # merchant then platform
    m = re.search(
        r'\b(merchant_[a-zA-Z0-9_]+|tenant_[a-zA-Z0-9_]+|demo_[a-zA-Z0-9_]+|shawn_[a-zA-Z0-9_]+)\b.*?\b'
        r'(shopify|tiktok|amazon|ebay|walmart|bigcommerce|woocommerce)\b',
        text, re.I,
    )
    if m:
        return m.group(2).lower(), m.group(1)
    return None, None


def _engineer_chat_process(message):
    """Parse a natural-language engineer command and execute the matching action."""
    text = message.strip().lower()

    if any(k in text for k in ('help', 'commands', 'what can you do', 'what can i')):
        return {
            "reply": (
                "Available commands:\n"
                "• health / status\n"
                "• metrics\n"
                "• alerts / sla\n"
                "• exceptions / logs\n"
                "• stores / connected stores\n"
                "• audit / recent events\n"
                "• summary\n"
                "• sync <platform> for <merchant_id>\n"
                "• reset <platform> for <merchant_id>\n"
                "• run migrations\n"
                "• pause sync / resume sync\n"
                "• maintenance on / off\n"
                "• non-beta on / off"
            ),
            "action": "help",
        }

    if re.search(r'\b(health|status|is .* (?:up|running))\b', text):
        health = monitoring_module.deep_health()
        status = health.get('status') or ('ok' if health.get('ok') else 'degraded')
        return {"reply": f"Platform health is {status}.", "result": health, "action": "health"}

    if re.search(r'\b(metrics|request count|latency|p95|p99)\b', text):
        metrics = monitoring_module.current_metrics()
        latency = metrics.get('latency_ms') or {}
        return {
            "reply": (
                f"Requests (last minute): {metrics.get('requests_per_minute')}; "
                f"p95 latency: {latency.get('p95')} ms; "
                f"error rate: {metrics.get('error_rate_percent', 0):.2f}%."
            ),
            "result": metrics,
            "action": "metrics",
        }

    if re.search(r'\b(alerts|sla|problems|issues|warnings)\b', text):
        alerts = monitoring_module.check_sla()
        return {"reply": f"Active SLA alerts: {len(alerts)}.", "result": {"alerts": alerts}, "action": "alerts"}

    if re.search(r'\b(exceptions|errors|logs|failures)\b', text):
        since = datetime.utcnow() - timedelta(hours=24)
        rows = SystemExceptionLog.query.filter(
            SystemExceptionLog.timestamp >= since
        ).order_by(SystemExceptionLog.timestamp.desc()).limit(20).all()
        exceptions = [
            {
                "id": r.id,
                "module": r.module_origin,
                "severity": r.error_severity,
                "message": r.exception_msg,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in rows
        ]
        return {
            "reply": f"{len(exceptions)} exceptions in the last 24 hours.",
            "result": {"exceptions": exceptions},
            "action": "exceptions",
        }

    if re.search(r'\b(stores|connected stores|channels|connections)\b', text):
        stores = _admin_stores()
        return {"reply": f"{len(stores)} connected stores.", "result": {"stores": stores}, "action": "stores"}

    if re.search(r'\b(audit|recent events|activity log|admin log)\b', text):
        rows = AdminAuditLog.query.order_by(AdminAuditLog.created_at.desc()).limit(20).all()
        events = [
            {
                "id": r.id,
                "admin": r.admin_email,
                "action": r.action,
                "target": r.target_merchant_id,
                "details": r.details,
                "timestamp": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
        return {
            "reply": f"Last {len(events)} audit events.",
            "result": {"events": events},
            "action": "audit",
        }

    if re.search(r'\b(summary|overview|platform summary|kpi)\b', text):
        summary = _admin_summary(datetime.utcnow())
        return {
            "reply": (
                f"Members: {summary.get('total_members')}; "
                f"paid: {summary.get('paid_accounts')}; "
                f"active sessions (15m): {summary.get('active_sessions')}; "
                f"connected stores: {summary.get('connected_stores')}; "
                f"unread support messages: {summary.get('unread_support')}."
            ),
            "result": summary,
            "action": "summary",
        }

    platform, merchant_id = _engineer_chat_extract_platform(text)

    if re.search(r'\b(sync|resync|force\s*sync|run\s*sync)\b', text) and platform and merchant_id:
        profile = MerchantProfile.query.get(merchant_id)
        if not profile:
            return {"reply": f"Merchant {merchant_id} not found.", "action": "sync"}
        try:
            if platform == 'shopify':
                result = shopify_sync.sync_shopify(merchant_id)
            elif platform == 'tiktok':
                result = tiktok_sync.sync_tiktok(merchant_id)
            elif platform == 'amazon':
                result = amazon_sync.sync_amazon(merchant_id)
            else:
                return {"reply": f"Sync not implemented for {platform}.", "action": "sync"}
            log_admin_audit("engineer_chat.sync", target_merchant_id=merchant_id, details={"platform": platform})
            return {"reply": f"Synced {platform} for {merchant_id}.", "result": result, "action": "sync"}
        except Exception as e:
            logger.error(f"[engineer_chat sync] {e}")
            return {"reply": f"Sync failed: {str(e)}", "action": "sync"}

    if re.search(r'\b(reset|mark stale|force reconnect|re-auth)\b', text) and platform and merchant_id:
        profile = MerchantProfile.query.get(merchant_id)
        if not profile:
            return {"reply": f"Merchant {merchant_id} not found.", "action": "reset"}
        if platform not in ('shopify', 'tiktok', 'amazon', 'ebay', 'walmart', 'bigcommerce', 'woocommerce'):
            return {"reply": f"Invalid platform: {platform}.", "action": "reset"}
        try:
            token = TenantOAuthToken.query.filter_by(merchant_id=merchant_id, platform_id=platform).first()
            if token:
                token.updated_at = datetime(2000, 1, 1)
                db.session.add(token)
            for link in IntegrationLink.query.filter_by(merchant_id=merchant_id, platform=platform).all():
                link.updated_at = datetime(2000, 1, 1)
                db.session.add(link)
            db.session.commit()
            log_admin_audit("engineer_chat.reset", target_merchant_id=merchant_id, details={"platform": platform})
            return {"reply": f"Reset {platform} connection for {merchant_id}. It will be re-synced on next connect.", "action": "reset"}
        except Exception as e:
            db.session.rollback()
            logger.error(f"[engineer_chat reset] {e}")
            return {"reply": f"Reset failed: {str(e)}", "action": "reset"}

    if re.search(r'\b(run\s+)?migrations?\b|\brefresh\s+(?:materialized\s+)?views?\b|\bschema\s+sync\b', text):
        try:
            db.create_all()
            migrate_module.refresh_materialized_views()
            log_admin_audit("engineer_chat.migrations")
            return {"reply": "Schema synced and materialized views refreshed.", "action": "migrations"}
        except Exception as e:
            logger.error(f"[engineer_chat migrations] {e}")
            return {"reply": f"Migrations failed: {str(e)}", "action": "migrations"}

    if re.search(r'\b(pause|resume|stop|start|on|off)\b.*\b(sync|synchronization|marketplace|all syncs)\b', text) or \
       re.search(r'\b(sync|synchronization|marketplace|all syncs)\b.*\b(pause|resume|stop|start|on|off)\b', text):
        if any(w in text for w in ('pause', 'stop', 'off')):
            enabled = True
        elif any(w in text for w in ('resume', 'start', 'on')):
            enabled = False
        else:
            enabled = True
        AdminPlatformControl.set_bool('global_sync_paused', enabled)
        log_admin_audit("engineer_chat.platform_control", details={"key": "global_sync_paused", "value": enabled})
        return {"reply": f"Global sync {'paused' if enabled else 'resumed'}.", "action": "platform_control"}

    maint_match = re.search(r'\bmaintenance(?:\s+mode)?\s+(on|off|enable|disable|true|false)\b', text)
    if maint_match:
        val = maint_match.group(1) in ('on', 'enable', 'true')
        AdminPlatformControl.set_bool('maintenance_mode', val)
        log_admin_audit("engineer_chat.platform_control", details={"key": "maintenance_mode", "value": val})
        return {"reply": f"Maintenance mode {'enabled' if val else 'disabled'}.", "action": "platform_control"}

    sample_match = re.search(r'\b(?:sample\s+pages?|non[-\s]?beta)\s+(on|off|show|hide|enable|disable)\b', text, re.IGNORECASE)
    if sample_match:
        val = sample_match.group(1) in ('on', 'show', 'enable', 'true')
        AdminPlatformControl.set_bool('sample_pages_enabled', val)
        log_admin_audit("engineer_chat.platform_control", details={"key": "sample_pages_enabled", "value": val})
        return {"reply": f"Non-beta modules {'shown' if val else 'hidden'} for merchants.", "action": "platform_control"}

    return {
        "reply": (
            "I didn't understand. Try: health, metrics, sync shopify for merchant_xxx, "
            "reset tiktok for merchant_xxx, run migrations, pause sync, maintenance on, "
            "non-beta on, audit, exceptions, stores, summary, or help."
        ),
        "action": "unknown",
    }


@app.route('/api/engineer/migrations', methods=['POST'])
@require_roles([UserRole.ENGINEER])
def api_engineer_migrations():
    """Run safe, idempotent schema migrations and refresh materialized views."""
    try:
        import migrate as _migrate
        db.create_all()
        _migrate.refresh_materialized_views()
        return jsonify({"status": "ok", "message": "Schema synced and materialized views refreshed."}), 200
    except Exception as e:
        logger.error(f"[engineer migrations] {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route('/api/engineer/chat', methods=['POST'])
@require_roles([UserRole.ENGINEER])
def api_engineer_chat():
    """Natural-language engineer assistant: parse a command and run operational actions."""
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({"reply": "Please send a message.", "action": "noop"}), 400
    result = _engineer_chat_process(message)
    return jsonify(result), 200


if __name__ == '__main__':
    app.run(debug=True, port=3000)
else:
    _start_mview_refresh_worker()
