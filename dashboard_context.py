"""
Shawnzyluxe Commerce AI — dashboard template.

Every panel is driven by the dicts below, so wiring a feature to real data means
replacing one function/constant and nothing else. Nothing here calls an external
API: the numbers are illustrative sample data, clearly labelled in the UI.
"""

import copy
import os
from datetime import datetime, timedelta, timezone

from flask import Flask, render_template, request, jsonify
from models import PredictiveLogistics
import profit_feed
import alert_matrix
import action_gate
import channels as channels_module


def _greeting_for(merchant=None):
    """Return a time-of-day greeting using the merchant's name."""
    name = (merchant or {}).get("name", "there")
    hour = datetime.now(timezone.utc).hour
    if hour < 12:
        salutation = "Good morning"
    elif hour < 17:
        salutation = "Good afternoon"
    elif hour < 21:
        salutation = "Good evening"
    else:
        salutation = "Good night"
    return f"{salutation}, {name}."


# Beta feature gating: when BETA_MODE=true, merchants only see beta-ready pages.
# Admins and engineers can still see every page so development can continue.
BETA_MODE = os.environ.get("BETA_MODE", "false").lower() in ("true", "1", "yes")
BETA_READY_PAGE_IDS = {
    "overview",
    "alerts",
    "action_gate",
    "profit_engine",
    "billing",
    "startup_pack",
    "commerce_hub",
    "tiktok_studio",
}


def _filter_nav_for_beta(nav_groups, user_role):
    """Return a copy of nav_groups with only beta-ready pages for merchants."""
    if not BETA_MODE or user_role in ("Admin", "Engineer"):
        return nav_groups
    filtered = []
    for group in nav_groups:
        links = [link for link in group["links"] if link.get("id") in BETA_READY_PAGE_IDS]
        if links:
            filtered.append({**group, "links": links})
    return filtered


BRAND = {
    "name": "Vantav",
    "product": "Dashboard",
    "owner": "there",
    "domain": "your-store.com",
}

# --------------------------------------------------------------------------
# Assistant headline panel
# --------------------------------------------------------------------------

COO = {
    "greeting": "Good morning, Shawn.",
    "summary": [
        ("Revenue is up", "18%", "this week"),
        ("Top seller projected to sell out in", "4 days", ""),
    ],
    "narrative": (
        "Switching Supplier B to Supplier C would save roughly $2,100 this month. "
        "Three delayed shipments should be proactively refunded to protect satisfaction scores. "
        "I have prepared the emails, the purchase order, and the inventory transfer."
    ),
    "plan": [
        {"n": 1, "what": "Email 3 customers with delayed shipments + 15% coupon", "impact": "Retention", "state": "ready"},
        {"n": 2, "what": "Purchase order: 240 units, Supplier C, 6-day lead", "impact": "-$2,100 cost", "state": "ready"},
        {"n": 3, "what": "Transfer 60 units Miami → Dallas warehouse", "impact": "1.4d faster", "state": "ready"},
        {"n": 4, "what": "Raise Product A ad budget by 20%", "impact": "+~$640 rev", "state": "needs review"},
    ],
    "confidence": 82,
}

# --------------------------------------------------------------------------
# AI Command Center
# --------------------------------------------------------------------------

COMMAND_SUGGESTIONS = [
    "How did I do today?",
    "Why are sales down?",
    "Which products should I discontinue?",
    "Show delayed orders.",
    "Create a discount campaign.",
    "Reorder my top 3 sellers.",
]

# Canned responses so the input does something visible before a model is wired in.
COMMAND_RESPONSES = {
    "how did i do today": {
        "answer": "Revenue $4,582 across 61 orders. Profit $1,394 (30.4% margin), up 6% on yesterday.",
        "did": ["Read 61 orders", "Recomputed margin", "Compared to 7-day mean"],
    },
    "why are sales down": {
        "answer": "Sales are not down overall — TikTok Shop fell 22% while Shopify rose 31%. TikTok CPM rose 41% after your top creative fatigued (frequency 4.8).",
        "did": ["Split revenue by channel", "Checked ad frequency", "Flagged creative fatigue"],
    },
    "which products should i discontinue": {
        "answer": "Two candidates: Chrome Tumbler 20oz (-4.1% net margin after refunds) and Linen Throw (sell-through 11% in 60 days, $1,840 tied up).",
        "did": ["Ranked by true margin", "Included refunds + storage", "Checked 60-day sell-through"],
    },
    "show delayed orders": {
        "answer": "3 orders past promised delivery: #1042 (6d, address mismatch), #1038 (5d, carrier delay), #1021 (5d, supplier backorder).",
        "did": ["Scanned open fulfillments", "Compared promise vs carrier ETA"],
    },
    "create a discount campaign": {
        "answer": "Drafted 'Weekend Lux 15' — 15% off 4 slow movers, capped at 200 redemptions, ends Sunday. Projected $1,180 revenue, $312 margin cost.",
        "did": ["Selected slow movers", "Modelled margin floor", "Prepared email + SMS copy"],
    },
}

# --------------------------------------------------------------------------
# Universal Commerce Hub
# --------------------------------------------------------------------------

CHANNELS = [
    {"name": "Shopify", "abbr": "SH", "color": "#5a8f3d", "state": "connected", "orders": 34, "revenue": 2610.40, "sync": "2 min ago"},
    {"name": "TikTok Shop", "abbr": "TT", "color": "#111827", "state": "connected", "orders": 14, "revenue": 902.15, "sync": "6 min ago"},
    {"name": "Amazon", "abbr": "AZ", "color": "#e07b00", "state": "connected", "orders": 9, "revenue": 741.00, "sync": "11 min ago"},
    {"name": "Etsy", "abbr": "ET", "color": "#f1641e", "state": "connected", "orders": 4, "revenue": 328.60, "sync": "18 min ago"},
    {"name": "eBay", "abbr": "EB", "color": "#0064d2", "state": "not connected", "orders": 0, "revenue": 0.0, "sync": "—"},
    {"name": "WooCommerce", "abbr": "WC", "color": "#7f54b3", "state": "not connected", "orders": 0, "revenue": 0.0, "sync": "—"},
    {"name": "Walmart", "abbr": "WM", "color": "#0071ce", "state": "not connected", "orders": 0, "revenue": 0.0, "sync": "—"},
    {"name": "BigCommerce", "abbr": "BC", "color": "#121118", "state": "not connected", "orders": 0, "revenue": 0.0, "sync": "—"},
]

# --------------------------------------------------------------------------
# Business Advisor — morning briefing
# --------------------------------------------------------------------------

BRIEFING = {
    "revenue": 4582.00,
    "profit": 1394.00,
    "orders": 61,
    "delayed": 3,
    "trending": ["Satin Sleep Set", "Chrome Water Bottle"],
    "action": "Increase ads on Product A by 20%.",
    "aov": 75.11,
    "refund_rate": 2.4,
}

# --------------------------------------------------------------------------
# Alerts — proactive notifications
# --------------------------------------------------------------------------

ALERTS = [
    {"level": "crit", "title": "Inventory will run out in 5 days", "detail": "Satin Sleep Set — 68 units left, selling 13/day. Supplier lead time is 6 days.", "when": "12 min ago", "actions": ["Create PO", "Snooze"]},
    {"level": "warn", "title": "Supplier prices increased 8%", "detail": "Supplier B raised unit cost $4.10 → $4.43 on 6 SKUs, effective next order.", "when": "1 h ago", "actions": ["Compare suppliers", "Accept"]},
    {"level": "warn", "title": "Refunds are unusually high", "detail": "Chrome Tumbler at 9.1% vs 2.4% store average — 4 of 6 cite 'lid leaks'.", "when": "3 h ago", "actions": ["Open cases", "Flag supplier"]},
    {"level": "crit", "title": "This product is becoming unprofitable", "detail": "Chrome Tumbler net margin -4.1% after refunds and return shipping.", "when": "5 h ago", "actions": ["Discontinue", "Reprice"]},
    {"level": "good", "title": "You should reorder now", "detail": "Reordering Linen Robe today lands stock 2 days before projected stockout.", "when": "Today", "actions": ["Create PO"]},
]

# --------------------------------------------------------------------------
# Profit Engine — true profit per order
# --------------------------------------------------------------------------

PROFIT_BREAKDOWN = [
    {"label": "Gross revenue", "amount": 4582.00, "kind": "in"},
    {"label": "Product cost", "amount": -1489.20, "kind": "out"},
    {"label": "Ad spend", "amount": -892.00, "kind": "out"},
    {"label": "Shipping", "amount": -418.55, "kind": "out"},
    {"label": "Transaction fees", "amount": -142.04, "kind": "out"},
    {"label": "Refunds", "amount": -110.00, "kind": "out"},
    {"label": "Taxes withheld", "amount": -136.21, "kind": "out"},
]

RECENT_ORDERS = [
    {"id": "#1042", "channel": "Shopify", "items": 2, "revenue": 128.00, "profit": 38.42, "margin": 30.0, "state": "delayed"},
    {"id": "#1041", "channel": "TikTok Shop", "items": 1, "revenue": 64.00, "profit": 11.90, "margin": 18.6, "state": "shipped"},
    {"id": "#1040", "channel": "Shopify", "items": 3, "revenue": 214.50, "profit": 79.10, "margin": 36.9, "state": "shipped"},
    {"id": "#1039", "channel": "Amazon", "items": 1, "revenue": 82.00, "profit": 14.05, "margin": 17.1, "state": "packed"},
    {"id": "#1038", "channel": "Etsy", "items": 2, "revenue": 96.40, "profit": 27.60, "margin": 28.6, "state": "delayed"},
    {"id": "#1037", "channel": "Shopify", "items": 1, "revenue": 58.00, "profit": -3.20, "margin": -5.5, "state": "refunded"},
]

# --------------------------------------------------------------------------
# Predictive Analytics
# --------------------------------------------------------------------------

SALES_SERIES = [
    {"day": "Mon", "value": 3810, "forecast": False},
    {"day": "Tue", "value": 4120, "forecast": False},
    {"day": "Wed", "value": 3990, "forecast": False},
    {"day": "Thu", "value": 4460, "forecast": False},
    {"day": "Fri", "value": 4582, "forecast": False},
    {"day": "Sat", "value": 5140, "forecast": True},
    {"day": "Sun", "value": 4870, "forecast": True},
]

FORECASTS = [
    {"label": "Next week revenue", "value": "$32,400", "note": "±7% · 12-week seasonality"},
    {"label": "Stockouts predicted", "value": "2 SKUs", "note": "Satin Sleep Set, Linen Robe"},
    {"label": "Seasonal demand", "value": "+24%", "note": "Gifting window opens in 3 weeks"},
    {"label": "Cash flow (30d)", "value": "$9,180", "note": "After $6,400 supplier outflow"},
    {"label": "Best restock date", "value": "Aug 9", "note": "Lands 2 days before stockout"},
]

# --------------------------------------------------------------------------
# Product Research
# --------------------------------------------------------------------------

RESEARCH = [
    {"product": "Silk Pillowcase Set", "signal": "Rising", "trend": 184, "margin": 64, "competition": "Low", "source": "TikTok + Etsy"},
    {"product": "Ceramic Diffuser", "signal": "Rising", "trend": 96, "margin": 58, "competition": "Medium", "source": "Amazon movers"},
    {"product": "Chrome Tumbler 20oz", "signal": "Declining", "trend": -38, "margin": 22, "competition": "High", "source": "Category saturation"},
    {"product": "Weighted Throw", "signal": "Watch", "trend": 12, "margin": 47, "competition": "Medium", "source": "Search volume"},
]

# --------------------------------------------------------------------------
# Fulfillment
# --------------------------------------------------------------------------

FULFILLMENT = {
    "routed_today": 58,
    "auto_rate": 94,
    "avg_saving": 1.86,
    "rows": [
        {"order": "#1042", "supplier": "Supplier C", "warehouse": "Dallas", "carrier": "UPS Ground", "cost": 6.40, "flag": "Address mismatch"},
        {"order": "#1041", "supplier": "Supplier A", "warehouse": "Miami", "carrier": "USPS Priority", "cost": 8.10, "flag": ""},
        {"order": "#1040", "supplier": "Supplier C", "warehouse": "Dallas", "carrier": "UPS Ground", "cost": 6.40, "flag": ""},
        {"order": "#1039", "supplier": "Supplier B", "warehouse": "Reno", "carrier": "FedEx Home", "cost": 7.25, "flag": "Fraud risk 61"},
    ],
}

# --------------------------------------------------------------------------
# Fraud Detection
# --------------------------------------------------------------------------

FRAUD = [
    {"order": "#1039", "score": 61, "reasons": "Billing/shipping mismatch · 3 cards tried", "verdict": "review"},
    {"order": "#1036", "score": 88, "reasons": "Freight forwarder address · velocity 5 orders/2 min", "verdict": "block"},
    {"order": "#1031", "score": 24, "reasons": "New customer, otherwise clean", "verdict": "allow"},
]

# --------------------------------------------------------------------------
# Supply Chain Intelligence
# --------------------------------------------------------------------------

SUPPLIERS = [
    {"name": "Supplier A", "price": 4.10, "ship_days": 8, "defect": 1.8, "refund": 2.1, "reliability": 88, "pick": False},
    {"name": "Supplier B", "price": 4.43, "ship_days": 7, "defect": 3.4, "refund": 4.0, "reliability": 74, "pick": False},
    {"name": "Supplier C", "price": 3.92, "ship_days": 6, "defect": 1.1, "refund": 1.4, "reliability": 94, "pick": True},
]

# --------------------------------------------------------------------------
# Marketing Studio
# --------------------------------------------------------------------------

STUDIO = [
    {"kind": "Product description", "title": "Satin Sleep Set", "meta": "3 variants · brand voice: warm luxe"},
    {"kind": "Email campaign", "title": "Weekend Lux 15", "meta": "Subject A/B ready · 4,120 recipients"},
    {"kind": "SMS campaign", "title": "Restock alert", "meta": "312 opted-in · 118 chars"},
    {"kind": "Ad copy", "title": "TikTok — hook set", "meta": "5 hooks · new creative angle"},
    {"kind": "Short-form video", "title": "Unboxing 12s", "meta": "Storyboard + captions"},
    {"kind": "SEO content", "title": "Best satin sheets", "meta": "1,400 words · 8 keywords"},
]

# --------------------------------------------------------------------------
# Customer Support
# --------------------------------------------------------------------------

SUPPORT = {
    "resolved_rate": 78,
    "open": 6,
    "escalated": 2,
    "avg_first_reply": "38s",
    "chats": 3,
    "sentiment": "94% Positive",
    "resolution": "Order #1204 tracking corrected autonomously.",
    "threads": [
        {"who": "Dana R.", "topic": "Where is my order?", "state": "auto-resolved", "note": "Tracking sent + ETA"},
        {"who": "Marc T.", "topic": "Return request", "state": "auto-resolved", "note": "Label issued, exchange offered"},
        {"who": "Priya S.", "topic": "Lid leaks", "state": "escalated", "note": "Defect pattern — routed to you"},
        {"who": "Alex M.", "topic": "Size exchange", "state": "auto-resolved", "note": "Swapped to L, no charge"},
    ],
}

MARKETING = {
    "campaign": "Summer Clearance Blast",
    "status": "Idle",
    "copy": "Awaiting generation trigger query text...",
}

DEFAULT_PREDICTIVE = [
    {
        "sku": "SZL-VAR-B",
        "days": 4,
        "velocity": 38.5,
        "restock": "2026-08-10",
        "flag": "CRITICAL_STOCKOUT",
    },
    {
        "sku": "SZL-VAR-A",
        "days": 22,
        "velocity": 12.1,
        "restock": "2026-08-28",
        "flag": "HEALTHY",
    },
]


def predictive_context():
    try:
        rows = PredictiveLogistics.query.order_by(PredictiveLogistics.days_remaining.asc()).all()
        if rows:
            return [
                {
                    "sku": r.variant_sku,
                    "days": r.days_remaining,
                    "velocity": r.forecasted_demand_velocity,
                    "restock": r.optimal_restock_date,
                    "flag": r.status_flag,
                }
                for r in rows
            ]
    except Exception:
        pass
    return DEFAULT_PREDICTIVE

STRIPE = {
    "plan": "Enterprise Plan",
    "usage": 4820,
    "invoice": 241.00,
}

CATALOG = {
    "title": "Premium Sample Product",
    "sku": "SZL-VAR-A",
    "price": 145.00,
}

# --------------------------------------------------------------------------
# Automation Builder
# --------------------------------------------------------------------------

AUTOMATIONS = [
    {"text": "If inventory falls below 20, notify me and create a purchase order.", "state": "active", "runs": 14},
    {"text": "If an order is delayed more than 5 days, email the customer and issue a coupon.", "state": "active", "runs": 3},
    {"text": "If refund rate on a SKU passes 6%, pause its ads and flag the supplier.", "state": "active", "runs": 1},
    {"text": "Every Monday, summarize channel performance and post it to Slack.", "state": "paused", "runs": 0},
]

# --------------------------------------------------------------------------
# Team
# --------------------------------------------------------------------------

SPECIALISTS = [
    {"name": "Marketing", "status": "Rewriting 5 TikTok hooks", "state": "working"},
    {"name": "Inventory", "status": "2 stockouts predicted", "state": "attention"},
    {"name": "Finance", "status": "Margin recomputed · 30.4%", "state": "idle"},
    {"name": "Logistics", "status": "58 orders routed today", "state": "working"},
    {"name": "Support", "status": "78% auto-resolved", "state": "working"},
    {"name": "Analytics", "status": "Forecast refreshed 06:10", "state": "idle"},
]

# --------------------------------------------------------------------------
# Business Health Score
# --------------------------------------------------------------------------

HEALTH = {
    "score": 74,
    "components": [
        {"label": "Profitability", "value": 68, "tone": ""},
        {"label": "Inventory health", "value": 52, "tone": "amber"},
        {"label": "Shipping performance", "value": 81, "tone": "green"},
        {"label": "Customer satisfaction", "value": 88, "tone": "green"},
        {"label": "Marketing efficiency", "value": 61, "tone": "amber"},
        {"label": "Cash flow", "value": 79, "tone": "green"},
    ],
    "recommendations": [
        "Reorder Satin Sleep Set today — biggest single lift to inventory health.",
        "Retire the fatigued TikTok creative; CPM is 41% above your 30-day mean.",
        "Drop Chrome Tumbler or raise it $6 — it is currently margin-negative.",
    ],
}

# --------------------------------------------------------------------------
# Global Commerce Tools
# --------------------------------------------------------------------------

GLOBAL_TOOLS = [
    {"label": "Currency conversion", "value": "7 currencies", "note": "Rates refreshed hourly"},
    {"label": "Tax calculation", "value": "Nexus: FL, TX", "note": "Auto-applied at checkout"},
    {"label": "Translation", "value": "4 languages", "note": "EN, ES, FR, DE"},
    {"label": "Duties & customs", "value": "Estimated", "note": "DDP quotes on 3 lanes"},
    {"label": "Multi-language storefront", "value": "2 live", "note": "EN, ES"},
]

# --------------------------------------------------------------------------
# Mobile Assistant
# --------------------------------------------------------------------------

MOBILE_ACTIONS = [
    "How much profit did I make today?",
    "Approve supplier order.",
    "Show delayed shipments.",
    "Create a discount.",
]

NAV = [
    ("Home", "/home", False),
    ("Overview", "#top", True),
    ("Command Center", "#command", False),
    ("Commerce Hub", "#channels", False),
    ("Alerts", "#alerts", False),
    ("Profit Engine", "#profit", False),
    ("Predictions", "#predict", False),
    ("Product Research", "#research", False),
    ("Fulfillment", "#fulfillment", False),
    ("Fraud", "#fraud", False),
    ("Suppliers", "#suppliers", False),
    ("Marketing Studio", "#studio", False),
    ("Support", "#support", False),
    ("Automations", "#automations", False),
    ("Team", "#team", False),
    ("Health Score", "#health", False),
    ("Mobile Assistant", "#mobile", False),
]

# New commercial-grade page navigation (matches mockups)
NAV_GROUPS = [
    {
        "label": "Workspace",
        "links": [
            {"id": "overview", "label": "Overview", "url": "/dashboard", "icon": "◈"},
            {"id": "command_center", "label": "Command Center", "url": "/dashboard/command-center", "icon": "◉"},
            {"id": "orders", "label": "Orders", "url": "/dashboard/orders", "icon": "◫", "badge": "24"},
            {"id": "customers", "label": "Customers", "url": "/dashboard/customers", "icon": "○"},
            {"id": "analytics", "label": "Analytics", "url": "/dashboard/analytics", "icon": "▤"},
        ],
    },
    {
        "label": "Commerce",
        "links": [
            {"id": "commerce_hub", "label": "Commerce Hub", "url": "/dashboard/commerce-hub", "icon": "☰"},
            {"id": "products", "label": "Products", "url": "/dashboard/products", "icon": "□"},
            {"id": "inventory", "label": "Inventory", "url": "/dashboard/inventory", "icon": "▣"},
            {"id": "shipments", "label": "Shipments", "url": "/dashboard/shipments", "icon": "✈"},
            {"id": "suppliers", "label": "Suppliers", "url": "/dashboard/suppliers", "icon": "▩"},
            {"id": "returns", "label": "Returns", "url": "/dashboard/returns", "icon": "↩"},
        ],
    },
    {
        "label": "Intelligence",
        "links": [
            {"id": "alerts", "label": "Alerts", "url": "/dashboard/alerts", "icon": "⚠", "badge": str(len(ALERTS))},
            {"id": "action_gate", "label": "Action Gate", "url": "/dashboard/action-gate", "icon": "✓"},
            {"id": "profit_engine", "label": "Profit Engine", "url": "/dashboard/profit-engine", "icon": "$"},
            {"id": "predictions", "label": "Predictions", "url": "/dashboard/predictions", "icon": "◐"},
            {"id": "product_research", "label": "Product Research", "url": "/dashboard/product-research", "icon": "◎"},
            {"id": "fulfillment", "label": "Fulfillment", "url": "/dashboard/fulfillment", "icon": "⛟"},
            {"id": "fraud", "label": "Fraud", "url": "/dashboard/fraud", "icon": "⚡"},
        ],
    },
    {
        "label": "Operations",
        "links": [
            {"id": "tiktok_studio", "label": "TikTok Studio", "url": "/dashboard/tiktok-studio", "icon": "✦"},
            {"id": "marketing", "label": "Marketing Studio", "url": "/dashboard/marketing", "icon": "✦"},
            {"id": "discounts", "label": "Discounts", "url": "/dashboard/discounts", "icon": "%"},
            {"id": "support", "label": "Support", "url": "/dashboard/support", "icon": "✉"},
            {"id": "automations", "label": "Automations", "url": "/dashboard/automations", "icon": "⏵"},
            {"id": "team_ai", "label": "Team", "url": "/dashboard/team-ai", "icon": "✦"},
            {"id": "health_score", "label": "Health Score", "url": "/dashboard/health-score", "icon": "♥"},
            {"id": "mobile_copilot", "label": "Mobile Assistant", "url": "/dashboard/mobile-copilot", "icon": "☎"},
        ],
    },
    {
        "label": "Store",
        "links": [
            {"id": "startup_pack", "label": "Brand Build", "url": "/dashboard/startup-pack", "icon": "☆"},
            {"id": "store_catalog", "label": "Store Catalog", "url": "/dashboard/store-catalog", "icon": "▤"},
            {"id": "apps", "label": "Apps", "url": "/dashboard/apps", "icon": "◫"},
            {"id": "themes", "label": "Themes", "url": "/dashboard/themes", "icon": "◉"},
            {"id": "reports", "label": "Reports", "url": "/dashboard/reports", "icon": "▦"},
            {"id": "billing", "label": "Billing", "url": "/dashboard/billing", "icon": "$"},
            {"id": "integrations", "label": "Integrations", "url": "/dashboard/integrations", "icon": "∞"},
            {"id": "settings", "label": "Settings", "url": "/dashboard/settings", "icon": "⚙"},
        ],
    },
]


def context(active_page=None, merchant=None, merchant_id=None):
    now = datetime.now(timezone.utc)
    user_role = (merchant or {}).get("role")
    # Use the real-time Profit Feed when a merchant is identified, otherwise fall
    # back to the static sample data so the dashboard still renders.
    feed = profit_feed.get_profit_breakdown(merchant_id) if merchant_id else None
    orders = profit_feed.get_recent_orders(merchant_id) if merchant_id else RECENT_ORDERS
    if feed is None:
        gross = sum(r["amount"] for r in PROFIT_BREAKDOWN if r["kind"] == "in")
        costs = -sum(r["amount"] for r in PROFIT_BREAKDOWN if r["kind"] == "out")
        net = gross - costs
        profit_rows = PROFIT_BREAKDOWN
    else:
        gross = feed["gross_revenue"]
        costs = feed["total_costs"]
        net = feed["net_profit"]
        profit_rows = feed["profit_rows"]
    # Always serve fresh headline numbers even if BRIEFING is mutated elsewhere.
    briefing = dict(BRIEFING)
    briefing["revenue"] = gross
    briefing["profit"] = net

    # Live Alert Matrix — refresh and format open alerts for the merchant.
    if merchant_id:
        try:
            alert_matrix.refresh_alerts(merchant_id)
            live_alerts = [alert_matrix.alert_to_dict(a) for a in alert_matrix.get_alerts(merchant_id)]
            fraud_alerts = [alert_matrix.fraud_alert_to_dict(a) for a in alert_matrix.get_fraud_alerts(merchant_id)]
        except Exception:
            live_alerts = ALERTS
            fraud_alerts = FRAUD
    else:
        live_alerts = ALERTS
        fraud_alerts = FRAUD

    # Dynamic nav badge reflects open alert count.
    nav_groups = copy.deepcopy(NAV_GROUPS)
    for group in nav_groups:
        for link in group["links"]:
            if link.get("id") == "alerts":
                link["badge"] = str(len(live_alerts))
    nav_groups = _filter_nav_for_beta(nav_groups, user_role)

    # Action Gate: draft approvals from open alerts.
    pending_actions = []
    action_history = []
    if merchant_id and (active_page == "action_gate" or active_page is None):
        try:
            pending_actions = [action_gate.action_to_dict(a) for a in action_gate.list_pending_actions(merchant_id)]
            action_history = [action_gate.action_to_dict(a) for a in action_gate.list_action_history(merchant_id)]
        except Exception:
            pass

    # Channel list from persistent connections.
    try:
        channel_data = channels_module.list_channels(merchant_id) if merchant_id else CHANNELS
    except Exception:
        channel_data = CHANNELS

    return {
        "brand": BRAND,
        "nav": NAV,
        "nav_groups": nav_groups,
        "active_page": active_page or "overview",
        "merchant": merchant or {
            "name": "Your store",
            "email": "admin@example.com",
            "tier": "Beta Plan",
            "sandbox_status": "approved",
            "live_access_enabled": True,
            "sandbox_expires_at": None,
        },
        "coo": dict(COO, greeting=_greeting_for(merchant)),
        "suggestions": COMMAND_SUGGESTIONS,
        "channels": channel_data,
        "connected": [c for c in channel_data if c.get("state") == "connected"],
        "briefing": briefing,
        "alerts": live_alerts,
        "profit_rows": profit_rows,
        "gross": gross,
        "costs": costs,
        "net": net,
        "net_margin": round(net / gross * 100, 1) if gross else 0.0,
        "orders": orders,
        "series": SALES_SERIES,
        "series_max": max(p["value"] for p in SALES_SERIES),
        "forecasts": FORECASTS,
        "research": RESEARCH,
        "fulfillment": FULFILLMENT,
        "fraud": fraud_alerts,
        "suppliers": SUPPLIERS,
        "studio": STUDIO,
        "support": SUPPORT,
        "mktg": MARKETING,
        "predictive": predictive_context(),
        "stripe": STRIPE,
        "catalog": CATALOG,
        "automations": AUTOMATIONS,
        "specialists": SPECIALISTS,
        "health": HEALTH,
        "global_tools": GLOBAL_TOOLS,
        "mobile_actions": MOBILE_ACTIONS,
        "pending_actions": pending_actions,
        "action_history": action_history,
        "generated": now.strftime("%A, %d %b %Y · %H:%M UTC"),
    }

