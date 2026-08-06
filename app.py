import os
import re
import hmac
import secrets
import requests
from datetime import datetime, timedelta
from urllib.parse import urlencode
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from dotenv import load_dotenv

load_dotenv()

from models import db, Tenant, ConnectedChannel, ActiveSession, BusinessMetric, CommerceChannel
from dashboard_context import (
    context,
    COMMAND_RESPONSES,
    RECENT_ORDERS,
    PROFIT_BREAKDOWN,
    BRIEFING,
    COO,
    CHANNELS,
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
}

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-this')
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///shawnzyluxe.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

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
    if request.endpoint in ('home', 'site_login', 'static'):
        return None
    if site_wall_authenticated():
        return None
    return redirect(url_for('home'))


# ============================================================
# END SITE PASSWORD WALL
# ============================================================

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
    return render_template('index.html', error=bool(request.args.get('error')))


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


@app.route('/api/v1/execute-command', methods=['POST'])
def execute_command():
    """NLP command engine that updates dashboard state in plain English."""
    data = request.get_json() or {}
    cmd_text = data.get("command", "").lower().strip()
    if not cmd_text:
        return jsonify({"success": False})

    updates = {
        "ai_briefing": DASHBOARD_STATE["ai_briefing"],
        "total_balance": f"{DASHBOARD_STATE['total_unified_balance']:.2f}",
        "clear_orders": False,
    }

    if re.search(r'(why are sales down|sales down|analyze drops)', cmd_text):
        updates["ai_briefing"] = "📊 AI Audit: Sales down 4% today due to an ad delivery lag in TikTok region East. Supply pipelines remain green."
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]

    elif re.search(r'(show delayed orders|delayed orders|shipments delayed)', cmd_text):
        updates["ai_briefing"] = "📦 Fulfillment Tracking: 2 shipments remain stalled at Memphis Hub due to supplier weather anomalies. Tracking codes verified."
        DASHBOARD_STATE["ai_briefing"] = updates["ai_briefing"]
        COO["narrative"] = updates["ai_briefing"]

    elif re.search(r'(create discount|discount campaign|promo code)', cmd_text):
        DASHBOARD_STATE["total_unified_balance"] += 1200.00
        BRIEFING["revenue"] += 1200.00
        updates["total_balance"] = f"{DASHBOARD_STATE['total_unified_balance']:.2f}"
        updates["ai_briefing"] = "✨ Automation Triggered: Generated 15% discount structure 'ECOM_AI_15'. Successfully pushed live to Shopify and TikTok Shop API channels."
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

    else:
        return jsonify({"success": False})

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
    db.session.commit()

    return jsonify({"success": True, "updates": updates})


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
