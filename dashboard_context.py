"""
Shawnzyluxe Commerce AI — dashboard template.

Every panel is driven by the dicts below, so wiring a feature to real data means
replacing one function/constant and nothing else. Nothing here calls an external
API: the numbers are illustrative sample data, clearly labelled in the UI.
"""

import copy
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from flask import Flask, render_template, request, jsonify
from sqlalchemy import func
from models import db, PredictiveLogistics, MerchantSetting, SaaSBilling, Product, OrderItem, UnifiedOrder
import profit_feed
import alert_matrix
import action_gate
import channels as channels_module
import channel_analytics
import tracking
import monitoring as monitoring_module


from zoneinfo import ZoneInfo


def _merchant_timezone(merchant_id=None) -> str:
    """Return the merchant's saved timezone, or UTC."""
    tz_name = "UTC"
    if merchant_id:
        tz = MerchantSetting.query.filter_by(merchant_id=merchant_id, setting_key="merchant_timezone").first()
        if tz and tz.setting_value:
            tz_name = tz.setting_value.strip()
    return tz_name


def _merchant_now(merchant_id=None):
    """Return the current datetime in the merchant's timezone."""
    tz_name = _merchant_timezone(merchant_id)
    try:
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now(timezone.utc)


def _evidence_for_action(action: Dict[str, Any], merchant_id: Optional[str] = None) -> List[str]:
    """Build human-readable evidence bullets from action payload, evidence table, and live snapshot."""
    evidence: List[str] = []
    payload = action.get("payload") or {}
    action_type = action.get("action_type", "")
    action_id = action.get("id")
    snapshot = None
    if merchant_id:
        try:
            import agent_context
            snapshot = agent_context.get_snapshot(merchant_id)
        except Exception:
            snapshot = None
    kpis = (snapshot.get("kpis") or {}) if snapshot else {}

    # Attach stored evidence if available.
    if action_id:
        try:
            from models import ActionEvidence
            ae = ActionEvidence.query.filter_by(action_id=action_id).first()
            if ae:
                evidence.append(f"Confidence: {ae.confidence_score}%")
                evidence.append(
                    f"Expected impact: ${float(ae.expected_weekly_impact_min or 0):,.2f}"
                    f"-${float(ae.expected_weekly_impact_max or 0):,.2f}/week"
                )
                if ae.reasoning_summary:
                    evidence.append(f"Why: {ae.reasoning_summary}")
                telemetry = ae.telemetry_evidence_log or {}
                if telemetry.get("margin") is not None:
                    evidence.append(f"Net margin at creation: {telemetry['margin']}%")
                if telemetry.get("orders"):
                    evidence.append(f"Orders in window: {telemetry['orders']}")
                if telemetry.get("competitor_median_price"):
                    trend = telemetry.get("market_trend", "flat")
                    evidence.append(
                        f"Market: median ${telemetry['competitor_median_price']:.2f}, "
                        f"trend {trend}, velocity {telemetry.get('sales_velocity_delta', 0):+.1f}%"
                    )
                if ae.execution_report:
                    evidence.append(f"Report: {ae.execution_report}")
        except Exception:
            pass

    if action_type == "ad_adjust":
        evidence.append(f"Platform: {payload.get('platform', 'ads')}")
        adj = payload.get("adjustment")
        if adj is not None:
            evidence.append(f"Recommended change: {adj}% budget")
        margin = kpis.get("net_margin")
        if margin is not None:
            evidence.append(f"Current net margin: {margin}%")
        revenue = kpis.get("gross_revenue")
        if revenue is not None:
            evidence.append(f"Gross revenue: ${revenue:,.0f}")
    elif action_type == "reorder":
        evidence.append(f"SKU: {payload.get('sku', 'unknown')}")
        evidence.append(f"Quantity: {payload.get('quantity', 'N/A')}")
        evidence.append(f"Supplier: {payload.get('supplier', 'N/A')} ({payload.get('lead_days', 'N/A')}-day lead)")
        orders = kpis.get("orders")
        if orders is not None:
            evidence.append(f"Recent orders: {orders}")
    elif action_type == "refund":
        evidence.append(f"Order: {payload.get('order_id', 'unknown')}")
        margin = kpis.get("net_margin")
        if margin is not None:
            evidence.append(f"Current net margin: {margin}%")
    else:
        for k, v in payload.items():
            evidence.append(f"{k.replace('_', ' ').title()}: {v}")
    if not evidence:
        evidence.append("AI evaluated this as a high-impact opportunity")
    return evidence


def _hero_action(pending_actions: List[Dict[str, Any]], merchant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the top pending action decorated with evidence for the overview."""
    if not pending_actions:
        return None
    hero = dict(pending_actions[0])
    hero["reasons"] = _evidence_for_action(hero, merchant_id)
    return hero


def _greeting_for(merchant=None):
    """Return a time-of-day greeting using the merchant's name and timezone."""
    name = (merchant or {}).get("name", "there")
    merchant_id = (merchant or {}).get("id")
    now = _merchant_now(merchant_id)
    hour = now.hour
    if hour < 5 or hour >= 22:
        salutation = "Good night"
    elif hour < 12:
        salutation = "Good morning"
    elif hour < 17:
        salutation = "Good afternoon"
    else:
        salutation = "Good evening"
    return f"{salutation}, {name}."


def _ai_greeting(merchant_id: Optional[str], merchant: Optional[Dict[str, Any]] = None) -> str:
    """Return a personalized AI greeting with revenue trend and top-seller projection."""
    name = (merchant or {}).get("name") or (merchant or {}).get("business_name") or "there"
    if not merchant_id:
        return f"Hi {name}, here's what's happening with your store today."
    try:
        now = _merchant_now(merchant_id)
        seven_ago = now - timedelta(days=7)
        fourteen_ago = now - timedelta(days=14)
        thirty_ago = now - timedelta(days=30)

        rev_now = db.session.query(func.coalesce(func.sum(UnifiedOrder.revenue), 0)).filter(
            UnifiedOrder.merchant_id == merchant_id,
            UnifiedOrder.created_at >= seven_ago,
            UnifiedOrder.created_at < now,
        ).scalar() or 0
        rev_prev = db.session.query(func.coalesce(func.sum(UnifiedOrder.revenue), 0)).filter(
            UnifiedOrder.merchant_id == merchant_id,
            UnifiedOrder.created_at >= fourteen_ago,
            UnifiedOrder.created_at < seven_ago,
        ).scalar() or 0
        rev_now = float(rev_now)
        rev_prev = float(rev_prev)
        if rev_prev:
            change = round(((rev_now - rev_prev) / rev_prev) * 100, 1)
        elif rev_now > 0:
            change = 100.0
        else:
            change = 0.0
        direction = "up" if change >= 0 else "down"

        top = db.session.query(
            Product.title,
            Product.sku,
            func.sum(OrderItem.qty).label("units"),
            func.sum(OrderItem.qty * OrderItem.unit_price).label("item_revenue"),
        ).join(OrderItem, Product.sku == OrderItem.sku
        ).join(UnifiedOrder, UnifiedOrder.id == OrderItem.order_id
        ).filter(
            Product.merchant_id == merchant_id,
            UnifiedOrder.merchant_id == merchant_id,
            UnifiedOrder.created_at >= thirty_ago,
        ).group_by(Product.sku, Product.title
        ).order_by(func.sum(OrderItem.qty * OrderItem.unit_price).desc()
        ).first()

        if top:
            units_last_7 = db.session.query(func.coalesce(func.sum(OrderItem.qty), 0)).join(
                UnifiedOrder, UnifiedOrder.id == OrderItem.order_id
            ).filter(
                OrderItem.sku == top.sku,
                UnifiedOrder.merchant_id == merchant_id,
                UnifiedOrder.created_at >= seven_ago,
            ).scalar() or 0
            projected = round(float(units_last_7))
            return f"Hi {name}, revenue is {direction} {abs(change)}% this week. Top seller: {top.title} — projected to sell {projected} more units in the next 7 days."
        return f"Hi {name}, revenue is {direction} {abs(change)}% this week."
    except Exception:
        return f"Hi {name}, here's what's happening with your store today."


# Commercial-ready feature gating: merchants only see the pages that are live and
# backed by real data. Admins and engineers can still see every page so development
# and internal demos can continue.
COMMERCIAL_READY_PAGE_IDS = {
    "overview",
    "command_center",
    "alerts",
    "action_gate",
    "profit_engine",
    "billing",
    "commerce_hub",
    "settings",
}


def _filter_nav_for_role(nav_groups, user_role):
    """Return a copy of nav_groups with only commercial-ready pages for merchants."""
    if user_role in ("Admin", "Engineer"):
        return nav_groups
    filtered = []
    for group in nav_groups:
        links = [link for link in group["links"] if link.get("id") in COMMERCIAL_READY_PAGE_IDS]
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
    "greeting": "Good morning, there.",
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
    {"platform": "shopify", "name": "Shopify", "abbr": "SP", "color": "#7AB55C", "state": "disconnected", "orders": 0, "revenue": 0.0, "sync": "Never"},
    {"platform": "tiktok", "name": "TikTok Shop", "abbr": "TT", "color": "#000000", "state": "disconnected", "orders": 0, "revenue": 0.0, "sync": "Never"},
    {"platform": "amazon", "name": "Amazon", "abbr": "AZ", "color": "#FF9900", "state": "disconnected", "orders": 0, "revenue": 0.0, "sync": "Never"},
    {"platform": "ebay", "name": "eBay", "abbr": "EB", "color": "#E53238", "state": "disconnected", "orders": 0, "revenue": 0.0, "sync": "Never"},
    {"platform": "walmart", "name": "Walmart", "abbr": "WM", "color": "#0071CE", "state": "disconnected", "orders": 0, "revenue": 0.0, "sync": "Never"},
    {"platform": "bigcommerce", "name": "BigCommerce", "abbr": "BC", "color": "#34313F", "state": "disconnected", "orders": 0, "revenue": 0.0, "sync": "Never"},
    {"platform": "woocommerce", "name": "WooCommerce", "abbr": "WC", "color": "#96588A", "state": "disconnected", "orders": 0, "revenue": 0.0, "sync": "Never"},
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

FULFILLMENT_ROWS = [
    {"order": "#1042", "supplier": "Supplier C", "warehouse": "Dallas", "carrier": "UPS Ground", "tracking_number": "1Z999AA10123456784", "cost": 6.40, "flag": "Address mismatch"},
    {"order": "#1041", "supplier": "Supplier A", "warehouse": "Miami", "carrier": "USPS Priority", "tracking_number": "9400111899223456789012", "cost": 8.10, "flag": ""},
    {"order": "#1040", "supplier": "Supplier C", "warehouse": "Dallas", "carrier": "UPS Ground", "tracking_number": "1Z999AA10123456784", "cost": 6.40, "flag": ""},
    {"order": "#1039", "supplier": "Supplier B", "warehouse": "Reno", "carrier": "FedEx Home", "tracking_number": "449044304137821", "cost": 7.25, "flag": "Fraud risk 61"},
]


def _fulfillment_rows():
    """Return fulfillment sample rows with computed tracking URLs."""
    rows = []
    for r in FULFILLMENT_ROWS:
        row = dict(r)
        row["tracking_url"] = tracking.tracking_url(row.get("tracking_number", ""), row.get("carrier", ""))
        row["carrier_key"] = tracking.detect_carrier(row.get("tracking_number", "")) or (row.get("carrier", "").split()[0].lower() if row.get("carrier") else "")
        rows.append(row)
    return rows


FULFILLMENT = {
    "routed_today": 58,
    "auto_rate": 94,
    "avg_saving": 1.86,
    "rows": _fulfillment_rows(),
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
    ("Vantav", "#command", False),
    ("Commerce Hub", "#channels", False),
    ("Alerts", "#alerts", False),
    ("Profit Dashboard", "#profit", False),
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
    ("Vantav Mobile", "#mobile", False),
]

# New commercial-grade page navigation (matches mockups)
NAV_GROUPS = [
    {
        "label": "Workspace",
        "links": [
            {"id": "overview", "label": "Overview", "url": "/dashboard", "icon": "◈"},
            {"id": "command_center", "label": "Vantav", "url": "/dashboard/command-center", "icon": "◉"},
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
            {"id": "profit_engine", "label": "Profit Dashboard", "url": "/dashboard/profit-engine", "icon": "$"},
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
            {"id": "mobile_copilot", "label": "Vantav Mobile", "url": "/dashboard/mobile-copilot", "icon": "☎"},
            {"id": "monitoring", "label": "Monitoring", "url": "/dashboard/monitoring", "icon": "◈"},
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
    tz_name = _merchant_timezone(merchant_id)
    now = _merchant_now(merchant_id)
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
    nav_groups = _filter_nav_for_role(nav_groups, user_role)

    # Admin-only backend navigation.
    if user_role in ("Admin", "Engineer"):
        nav_groups.append({
            "label": "Admin",
            "links": [
                {"id": "admin_merchants", "label": "Merchants", "url": "/admin/merchants", "icon": "⚙"},
            ],
        })

    # Action Gate: draft approvals from open alerts.
    pending_actions = []
    action_history = []
    if merchant_id and (active_page in ("overview", "action_gate") or active_page is None):
        try:
            pending_actions = [action_gate.action_to_dict(a) for a in action_gate.list_pending_actions(merchant_id)]
            action_history = [action_gate.action_to_dict(a) for a in action_gate.list_action_history(merchant_id)]
        except Exception:
            pass
    hero_action = _hero_action(pending_actions, merchant_id)

    # Channel true-profit analytics.
    channel_summary = []
    channel_totals = {}
    if merchant_id:
        try:
            channel_summary = channel_analytics.summarize_channels(merchant_id, days=30)
            channel_totals = channel_analytics.channel_totals(merchant_id, days=30)
        except Exception:
            pass

    # Billing context for the dashboard Billing page.
    billing_account = {"approved_actions": 0}
    tier_limits = {}
    if merchant_id:
        try:
            from tier_manager import TierManager
            billing = SaaSBilling.query.get(merchant_id)
            tier_key = (merchant_obj.get("tier") or "Basic Tier").replace("AI Tier", "Plan").strip()
            meta = TierManager.get_tier_meta(tier_key)
            billing_account = {
                "current_plan": billing.current_plan if billing else tier_key,
                "add_ons": billing.add_ons if billing else [],
                "concierge_bundle": "concierge_bundle" in (billing.add_ons if billing else []),
                "stripe_customer_id": billing.stripe_customer_id if billing else "",
                "stripe_subscription_id": billing.stripe_subscription_item_id if billing else "",
                "metered_usage_units": billing.metered_usage_units if billing else 0,
                "approved_actions": TierManager.current_action_count(merchant_id),
                "accrued_invoice_value": billing.accrued_invoice_value if billing else 0.0,
                "billing_cycle_end": billing.billing_cycle_end if billing else "",
            }
            tier_limits = {
                "orders": meta.get("monthly_order_limit", 500),
                "actions": meta.get("max_monthly_actions", 50),
                "stores": meta.get("max_store_connections", 2),
                "users": meta.get("max_users", 1),
                "products": 10000,
                "customers": 100000,
                "storage": 500,
            }
        except Exception:
            pass

    # Channel list from persistent connections.
    try:
        channel_data = channels_module.list_channels(merchant_id) if merchant_id else CHANNELS
    except Exception:
        channel_data = CHANNELS

    # Ensure the merchant dict carries the timezone so templates can render local time.
    merchant_obj = dict(merchant or {
        "name": "Your store",
        "email": "admin@example.com",
        "tier": "Beta Plan",
        "sandbox_status": "approved",
        "live_access_enabled": True,
        "sandbox_expires_at": None,
    })
    merchant_obj.setdefault("timezone", tz_name)

    return {
        "brand": BRAND,
        "nav": NAV,
        "nav_groups": nav_groups,
        "active_page": active_page or "overview",
        "merchant": merchant_obj,
        "coo": dict(COO, greeting=_greeting_for(merchant_obj)),
        "ai_greeting": _ai_greeting(merchant_id, merchant_obj),
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
        "hero_action": hero_action,
        "channel_summary": channel_summary,
        "channel_totals": channel_totals,
        "billing_account": billing_account,
        "tier_limits": tier_limits,
        "team_users": [],
        "thresholds": {
            "slow_p95_ms": monitoring_module.SLOW_P95_MS,
            "error_rate_threshold": monitoring_module.ERROR_RATE_THRESHOLD,
            "db_latency_ms": monitoring_module.DB_LATENCY_MS_THRESHOLD,
            "max_pending_actions": monitoring_module.MAX_PENDING_ACTIONS,
            "max_channel_sync_age_seconds": monitoring_module.MAX_CHANNEL_SYNC_AGE_SECONDS,
        },
        "alert_config": {
            "email": ", ".join(monitoring_module.ALERT_EMAILS) or None,
            "phone": monitoring_module.ALERT_PHONE or None,
            "discord": bool(monitoring_module.DISCORD_WEBHOOK_URL),
            "webhook": bool(monitoring_module.SLA_WEBHOOK_URL),
        },
        "generated": now.strftime("%A, %d %b %Y · %H:%M %Z"),
    }

