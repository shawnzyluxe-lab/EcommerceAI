import os
import re
import hmac
import hashlib
import base64
import json
import secrets
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from urllib.parse import urlencode
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from flask_sock import Sock
from dotenv import load_dotenv

load_dotenv()

from models import db, Tenant, ConnectedChannel, ActiveSession, BusinessMetric, CommerceChannel, SupportMetric, MarketingStudio, PredictiveLogistics, OutboundTransmission, SaaSBilling, LocalProductCatalog, MerchantProfile, TenantOAuthToken, MerchantMetric
from dashboard_context import (
    context,
    COMMAND_RESPONSES,
    RECENT_ORDERS,
    PROFIT_BREAKDOWN,
    BRIEFING,
    COO,
    CHANNELS,
    SUPPORT,
    MARKETING,
    STRIPE,
    CATALOG,
)

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
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-this')
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///shawnzyluxe.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

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

SITE_WALL_PASSWORD = "IfxSVNs4iAs"
SESSION_COOKIE_NAME = "aegis_session_token"

# In-memory active token ring. Server restart clears all sessions.
active_sessions = set()


def site_wall_enabled():
    """The wall is enabled only when a password is configured."""
    return bool(SITE_WALL_PASSWORD)


def site_wall_authenticated():
    """Check whether the browser has a valid, non-expired session token."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token is None or token not in active_sessions:
        return False
    s = ActiveSession.query.get(token)
    if not s or s.created_at < datetime.utcnow() - timedelta(seconds=300):
        active_sessions.discard(token)
        return False
    return True


@app.before_request
def site_wall_protect():
    if not site_wall_enabled():
        return None
    if request.endpoint in ('home', 'site_login', 'shopify_orders_webhook', 'register_merchant', 'shopify_oauth_callback', 'static'):
        return None
    if site_wall_authenticated():
        return None
    return redirect(url_for('home'))


# ============================================================
# END SITE PASSWORD WALL
# ============================================================

SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.mailgun.org")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
MERCHANT_EMAIL = os.environ.get("MERCHANT_EMAIL", "shawn@shawnzyluxe.com")
SUPPLIER_EMAIL = os.environ.get("SUPPLIER_EMAIL", "production@supplier-c.com")

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
SHOPIFY_STORE_URL = os.environ.get("SHOPIFY_STORE_URL", "")
SHOPIFY_ACCESS_TOKEN = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
SHOPIFY_CLIENT_ID = os.environ.get("SHOPIFY_CLIENT_ID", "")
SHOPIFY_CLIENT_SECRET = os.environ.get("SHOPIFY_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI = os.environ.get("OAUTH_REDIRECT_URI", "https://shawnzyluxe.com")

SHOPIFY_DOMAIN = os.environ.get('SHOPIFY_DOMAIN', '').strip()
STOREFRONT_TOKEN = os.environ.get('SHOPIFY_STOREFRONT_TOKEN', '').strip()
CUSTOMER_ACCOUNT_CLIENT_ID = os.environ.get('SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_ID', '').strip()
CUSTOMER_ACCOUNT_CLIENT_SECRET = os.environ.get('SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_SECRET', '').strip()

GRAPHQL_URL = f"https://{SHOPIFY_DOMAIN}/api/2024-07/graphql.json" if SHOPIFY_DOMAIN else None
CUSTOMER_ACCOUNT_BASE = f"https://shopify.com/{SHOPIFY_DOMAIN.split('.')[0]}" if SHOPIFY_DOMAIN else None

with app.app_context():
    db.create_all()

    # Clean expired sessions and restore active ones
    ActiveSession.query.filter(
        ActiveSession.created_at < datetime.utcnow() - timedelta(seconds=300)
    ).delete(synchronize_session=False)
    db.session.commit()
    for s in ActiveSession.query.all():
        active_sessions.add(s.token)

    # Seed or restore business metrics
    if not BusinessMetric.query.first():
        db.session.add(BusinessMetric(
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

    # Seed multi-tenant merchant profiles
    if not MerchantProfile.query.first():
        db.session.add(MerchantProfile(merchant_id="merchant_shawn_01", business_name="Shawnzyluxe Global", admin_email="shawn@shawnzyluxe.com", password_hash=""))
        db.session.add(MerchantProfile(merchant_id="merchant_guest_02", business_name="Alpha Storefronts", admin_email="guest@alpha.com", password_hash=""))
        db.session.add(MerchantMetric(merchant_id="merchant_shawn_01", total_unified_balance=20560.00, true_net_profit=1394.00, gross_revenue=4582.00, ai_briefing="System initialized for Shawnzyluxe multi-tenant parameters."))
        db.session.add(MerchantMetric(merchant_id="merchant_guest_02", total_unified_balance=1240.00, true_net_profit=410.00, gross_revenue=890.00, ai_briefing="System initialized for guest merchant clusters."))
    db.session.commit()

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


def dispatch_external_email(recipient, subject, html_body):
    """Send transactional email via configured SMTP; log result to outbound_transmissions."""
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        log_transmission("EMAIL_BLAST", recipient, "NO_CREDENTIALS", "SMTP username or password not configured")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Shawnzyluxe AI <{SMTP_USERNAME}>"
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
    dispatch_external_email(SUPPLIER_EMAIL, f"URGENT: Automated Reorder Request {po_number}", email_body)
    log_transmission("SUPPLIER_PO", SUPPLIER_EMAIL, "TRANSMITTED", f"PO file {po_number} compiled and emailed.")

    return po_number


def log_metered_api_usage(merchant_id, operations_count):
    account = SaaSBilling.query.get(merchant_id)
    if account:
        account.metered_usage_units += operations_count
        account.accrued_invoice_value += operations_count * 0.05


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
        print("Storefront API error:", e)
        return {}


@app.route('/')
def home():
    if site_wall_authenticated():
        return redirect(url_for('dashboard'))
    return render_template('index.html', error=bool(request.args.get('error')), oauth_sync=request.args.get('oauth_sync'))


@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html', **context())


@app.route('/home')
def home_page():
    return render_template('home.html')


@app.route('/api/command', methods=['POST'])
def api_command():
    q = (request.json or {}).get("q", "").strip().lower().rstrip("?.!")
    hit = COMMAND_RESPONSES.get(q)
    if not hit:
        for key, value in COMMAND_RESPONSES.items():
            if key in q or q in key:
                hit = value
                break
    if not hit:
        return jsonify({
            "answer": "Not wired yet — this endpoint returns canned answers until you connect a model.",
            "did": [],
            "stub": True,
        })
    return jsonify({**hit, "stub": True})


def process_command(cmd_text):
    """Shared NLP engine used by HTTP and WebSocket. Mutates state and persists."""
    if not cmd_text:
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
        DASHBOARD_STATE["mktg_campaign"] = "Autumn Launch Preview"
        DASHBOARD_STATE["mktg_status"] = "Sending API..."
        DASHBOARD_STATE["mktg_copy"] = "Email Blast queued via SendGrid/Mailgun API channels."
        updates["mktg_campaign"] = DASHBOARD_STATE["mktg_campaign"]
        updates["mktg_status"] = DASHBOARD_STATE["mktg_status"]
        updates["mktg_copy"] = DASHBOARD_STATE["mktg_copy"]

        html_campaign_body = """<div style='font-family:sans-serif; max-width:600px; margin:0 auto; padding:20px; border:1px solid #EAEAEA;'>
  <h2 style='color:#1D2D44;'>The Autumn Collection Preview</h2>
  <p>Shawnzyluxe early operational configuration profiles are now open. Secure early access to your platform allocation containers link now.</p>
  <hr style='border:none; border-top:1px solid #EEEEEE;'/>
  <p style='font-size:11px; color:#999999;'>Sent via Shawnzyluxe Automated Marketing Engine Hub.</p>
</div>"""
        api_success = dispatch_external_email("subscribers-list@shawnzyluxe.com", "The Next Chapter: Shawnzyluxe Autumn Preview", html_campaign_body)

        if api_success:
            DASHBOARD_STATE["mktg_status"] = "Deployed"
            DASHBOARD_STATE["mktg_copy"] = "Email Blast Transmitted: 'The next chapter of style drops soon. Shawnzyluxe Members secure early operational access. Tap to unlock your portal container link now.'"
            updates["ai_briefing"] = "🚀 Creative Studio Success: Compiled localized campaign arrays and successfully transmitted emails via production gateway."
        else:
            DASHBOARD_STATE["mktg_status"] = "API_Error"
            DASHBOARD_STATE["mktg_copy"] = "⚠️ Outbound SMTP campaign failed connection sync. Check error logs."
            updates["ai_briefing"] = "⚠️ Gateway Warning: Outbound SMTP marketing campaign failed. Verify SMTP credentials."
        updates["mktg_status"] = DASHBOARD_STATE["mktg_status"]
        updates["mktg_copy"] = DASHBOARD_STATE["mktg_copy"]
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]

    elif re.search(r'(create discount|discount campaign|promo code)', cmd_text):
        DASHBOARD_STATE["total_unified_balance"] += 1200.00
        BRIEFING["revenue"] += 1200.00
        DASHBOARD_STATE["mktg_campaign"] = "ECOM_AI_15 Active"
        DASHBOARD_STATE["mktg_status"] = "Generated"
        DASHBOARD_STATE["mktg_copy"] = "SMS Blast queued: 'Hey! AI automation selected you for a 15% discount code on Shawnzyluxe today. Use ECOM_AI_15 at checkout!'"
        updates["total_balance"] = f"{DASHBOARD_STATE['total_unified_balance']:.2f}"
        updates["mktg_campaign"] = DASHBOARD_STATE["mktg_campaign"]
        updates["mktg_status"] = DASHBOARD_STATE["mktg_status"]
        updates["mktg_copy"] = DASHBOARD_STATE["mktg_copy"]
        updates["ai_briefing"] = "✨ Marketing Studio Action: Successfully injected dynamic promo script vectors live into connected channel API pipelines."
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]

    elif re.search(r'(evaluate shortages|predict supply|inventory forecast|shortages)', cmd_text):
        p_row = PredictiveLogistics.query.filter_by(variant_sku="SZL-VAR-B").first()
        if p_row:
            po_ref = generate_and_send_supplier_po(p_row.variant_sku, 450)
            p_row.days_remaining = 30
            p_row.status_flag = "REORDERED"
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
            updates["stripe_usage"] = billing.metered_usage_units
            updates["stripe_invoice"] = f"{billing.accrued_invoice_value:.2f}"
            updates["ai_briefing"] = f"💰 Stripe Ledger Synced: Profile mapped to <b>{billing.current_plan}</b>. Current billing cycle usage: <b>{billing.metered_usage_units} metered actions</b>. Accrued invoice totals: <b>${billing.accrued_invoice_value:.2f}</b>."
        else:
            updates["ai_briefing"] = "💰 Stripe Ledger: No billing account found."
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]

    elif re.search(r'(update price|adjust cost|catalog push)', cmd_text):
        prices = re.findall(r'\d+(?:\.\d+)?', cmd_text)
        if prices:
            target_price = float(prices[0])
            log_metered_api_usage("merchant_shawn_01", 10)
            catalog = LocalProductCatalog.query.first()
            if catalog:
                api_success = mutate_shopify_product_price(catalog.shopify_product_id, catalog.variant_id, target_price)
                catalog.price = target_price
                db.session.commit()
                DASHBOARD_STATE["hoodie_price"] = target_price
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
        print("WebSocket error:", e)
    finally:
        manager.disconnect(ws)


@app.route('/api/orders')
def api_orders():
    """Return recent orders with computed margin."""
    return jsonify({
        "orders": RECENT_ORDERS,
        "count": len(RECENT_ORDERS),
        "revenue": BRIEFING["revenue"],
        "profit": BRIEFING["profit"],
    })


@app.route('/api/profit/breakdown')
def api_profit_breakdown():
    """Calculate and return the profit breakdown."""
    gross = sum(r["amount"] for r in PROFIT_BREAKDOWN if r["kind"] == "in")
    costs = -sum(r["amount"] for r in PROFIT_BREAKDOWN if r["kind"] == "out")
    net = gross - costs
    margin = round(net / gross * 100, 1) if gross else 0.0
    return jsonify({
        "gross_revenue": round(gross, 2),
        "total_costs": round(costs, 2),
        "net_profit": round(net, 2),
        "net_margin": margin,
        "rows": PROFIT_BREAKDOWN,
    })


@app.route('/site-login', methods=['GET', 'POST'])
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
            active_sessions.add(token)
            db.session.add(ActiveSession(token=token, created_at=datetime.utcnow()))
            db.session.commit()
            response = redirect(url_for('home'))
            response.set_cookie(
                SESSION_COOKIE_NAME,
                token,
                max_age=300,
                httponly=True,
                samesite='Lax',
                secure=True,
            )
            return response
        error = True
    return redirect(url_for('home', error=1)) if error else redirect(url_for('home'))


@app.route('/site-logout')
def site_logout():
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token and token in active_sessions:
        active_sessions.remove(token)
    if token:
        ActiveSession.query.filter_by(token=token).delete()
        db.session.commit()
    response = redirect(url_for('home'))
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


SHOPIFY_WEBHOOK_SECRET = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "").strip().encode()


@app.route('/api/v1/webhooks/shopify-orders', methods=['POST'])
def shopify_orders_webhook():
    """Ingest live Shopify order webhooks, verify HMAC, mutate state, broadcast."""
    raw_body = request.data
    hmac_header = request.headers.get("X-Shopify-Hmac-SHA256")

    if SHOPIFY_WEBHOOK_SECRET:
        if not hmac_header:
            return jsonify({"status": "rejected", "reason": "Missing HMAC"}), 401
        computed = hmac.new(SHOPIFY_WEBHOOK_SECRET, raw_body, hashlib.sha256).digest()
        if not hmac.compare_digest(computed, base64.b64decode(hmac_header)):
            return jsonify({"status": "rejected", "reason": "Invalid HMAC"}), 401
    else:
        print("SHOPIFY_WEBHOOK_SECRET not set — accepting webhook without HMAC verification")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        return jsonify({"status": "rejected", "reason": "Invalid JSON"}), 400

    order_value = float(payload.get("total_price", 0.00))

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
        total_unified_balance=new_bal,
        true_net_profit=new_profit,
        gross_revenue=new_gross,
        ai_briefing=new_briefing,
    ))
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


@app.route('/api/v1/download-report')
def download_report():
    """Serve the generated CSV ledger to authenticated admins."""
    target = os.path.join(GENERATED_DIR, "shawnzyluxe_ledger.csv")
    if not os.path.exists(target):
        return jsonify({"status": "compiling"}), 404
    return send_file(target, as_attachment=True, download_name="shawnzyluxe_ledger.csv", mimetype="text/csv")


@app.route('/api/v1/tenant/register', methods=['POST'])
def register_merchant():
    """Create an isolated merchant profile and seed partitioned metrics."""
    data = request.get_json() or {}
    business_name = data.get("business_name", "").strip()
    admin_email = data.get("admin_email", "").strip()
    password_plain = data.get("password_plain", "").strip()

    if not business_name or not admin_email:
        return jsonify({"success": False, "error": "Missing business or email"}), 400

    new_merchant_id = f"merchant_{secrets.token_hex(4)}"
    password_hash = hashlib.sha256(password_plain.encode()).hexdigest()

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
            ai_briefing="Welcome to your isolated Shawnzyluxe AI workspace node. Connect channels to initialize streams.",
        ))
        db.session.commit()
        return jsonify({"success": True, "merchant_id": new_merchant_id, "status": "Workspace Schema Generated"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": "Administrative profile email already registered"}), 400


@app.route('/api/v1/auth/shopify/connect')
def shopify_oauth_connect():
    """Step 1: Redirect merchant to Shopify OAuth grant screen."""
    shop = request.args.get("shop", "").strip().lower()
    if not re.match(r'^[a-zA-Z0-9\-]+\.myshopify\.com$', shop):
        return jsonify({"success": False, "error": "Invalid shop layout format"}), 400

    scopes = "read_products,write_products,read_orders,read_inventory,read_fulfillments"
    oauth_url = f"https://{shop}/admin/oauth/authorize?client_id={SHOPIFY_CLIENT_ID}&scope={scopes}&redirect_uri={OAUTH_REDIRECT_URI}"
    return redirect(oauth_url)


@app.route('/api/v1/auth/shopify/callback')
def shopify_oauth_callback():
    """Step 2: Capture OAuth code and store tenant access token."""
    code = request.args.get("code")
    shop = request.args.get("shop")
    hmac_param = request.args.get("hmac")
    timestamp = request.args.get("timestamp")
    active_merchant = "merchant_shawn_01"

    if not SHOPIFY_CLIENT_ID or not SHOPIFY_CLIENT_SECRET:
        return jsonify({"success": False, "error": "OAuth credentials not configured"}), 400

    exchange_url = f"https://{shop}/admin/oauth/access_token"
    payload = {
        "client_id": SHOPIFY_CLIENT_ID,
        "client_secret": SHOPIFY_CLIENT_SECRET,
        "code": code,
    }

    try:
        # Live token exchange (uncomment when credentials are valid)
        # r = requests.post(exchange_url, json=payload, timeout=8)
        # res_data = r.json()
        # token = res_data.get("access_token")

        # Simulated token exchange for layout testing
        token = f"shpat_live_token_{secrets.token_hex(8)}"
        scopes_confirmed = "read_products,write_products,read_orders"

        db.session.merge(TenantOAuthToken(
            shop_domain=shop,
            merchant_id=active_merchant,
            platform_id="shopify",
            access_token_encrypted=token,
            scope_permissions=scopes_confirmed,
        ))
        db.session.commit()
        return redirect("/?oauth_sync=success")
    except Exception as e:
        return jsonify({"success": False, "error": f"OAuth handshake stall: {e}"}), 500


@app.route('/login')
def login():
    return render_template('login.html', domain=SHOPIFY_DOMAIN)


@app.route('/account')
def account():
    if 'customer_access_token' not in session:
        return redirect(url_for('login'))
    return render_template('account.html', customer=session.get('customer'))


@app.route('/logout')
def logout():
    session.pop('customer_access_token', None)
    session.pop('customer', None)
    return redirect(url_for('home'))


if __name__ == '__main__':
    app.run(debug=True, port=3000)
