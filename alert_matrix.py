"""Alert Matrix for Vantav.

Generates, persists, and dispatches real-time operational alerts:
- Inventory runout / low-stock warnings from PredictiveLogistics
- Fraud / refund / cancellation signals from ProfitFeedOrder
- Ad spend thresholds from AdSpendAnalytic

Dispatches to Discord webhooks and Twilio SMS when configured.
"""
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from models import db, Alert, PredictiveLogistics, ProfitFeedOrder, AdSpendAnalytic

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK_URL = os.environ.get("ALERT_DISCORD_WEBHOOK_URL", "")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")


def _days_text(days):
    if days is None:
        return "unknown"
    if days <= 0:
        return "now"
    if days == 1:
        return "1 day"
    return f"{days} days"


def _relative_when(dt):
    if not dt:
        return "just now"
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    if delta < timedelta(seconds=60):
        return "just now"
    if delta < timedelta(minutes=60):
        return f"{int(delta.total_seconds() // 60)} min ago"
    if delta < timedelta(hours=24):
        return f"{int(delta.total_seconds() // 3600)} h ago"
    if delta < timedelta(days=7):
        return f"{delta.days} day{'s' if delta.days != 1 else ''} ago"
    return dt.strftime("%b %d")


def generate_inventory_alerts(merchant_id="merchant_shawn_01"):
    """Create inventory alerts from PredictiveLogistics rows."""
    rows = PredictiveLogistics.query.order_by(PredictiveLogistics.days_remaining.asc()).all()
    created = []
    for row in rows:
        if row.days_remaining is None:
            continue
        source_id = f"pl:{row.variant_sku}"
        if row.days_remaining <= 5 or row.status_flag == "CRITICAL_STOCKOUT":
            existing = Alert.query.filter_by(
                merchant_id=merchant_id, alert_type="inventory_runout", source_id=source_id, status="open"
            ).first()
            if not existing:
                a = Alert(
                    merchant_id=merchant_id,
                    alert_type="inventory_runout",
                    severity="crit",
                    title=f"Inventory will run out in {_days_text(row.days_remaining)}",
                    detail=f"{row.variant_sku} — {row.days_remaining} days remaining at velocity {row.forecasted_demand_velocity or 0:.1f}/day.",
                    source_id=source_id,
                    status="open",
                )
                db.session.add(a)
                created.append(a)
        elif row.days_remaining <= 14:
            existing = Alert.query.filter_by(
                merchant_id=merchant_id, alert_type="low_inventory", source_id=source_id, status="open"
            ).first()
            if not existing:
                a = Alert(
                    merchant_id=merchant_id,
                    alert_type="low_inventory",
                    severity="warn",
                    title=f"Low inventory — {_days_text(row.days_remaining)} of stock left",
                    detail=f"{row.variant_sku} is forecasted to stock out in {row.days_remaining} days.",
                    source_id=source_id,
                    status="open",
                )
                db.session.add(a)
                created.append(a)
    db.session.commit()
    return created


def generate_fraud_alerts(merchant_id="merchant_shawn_01"):
    """Create fraud / refund / cancellation alerts from ProfitFeedOrder."""
    since = datetime.now(timezone.utc) - timedelta(days=30)
    orders = ProfitFeedOrder.query.filter(
        ProfitFeedOrder.merchant_id == merchant_id,
        ProfitFeedOrder.recorded_at >= since,
    ).all()

    created = []
    for order in orders:
        if order.state in ("cancelled", "refunded"):
            source_id = f"order:{order.order_id}"
            existing = Alert.query.filter_by(
                merchant_id=merchant_id, alert_type="fraud_risk", source_id=source_id, status="open"
            ).first()
            if not existing:
                a = Alert(
                    merchant_id=merchant_id,
                    alert_type="fraud_risk",
                    severity="crit" if order.state == "cancelled" else "warn",
                    title=f"{order.state.title()} order on {order.channel}",
                    detail=f"Order {order.order_id} ({order.gross_revenue:.2f}) is in {order.state} state.",
                    source_id=source_id,
                    status="open",
                )
                db.session.add(a)
                created.append(a)

    # Flag a SKU or channel if refund/cancellation rate is unusually high.
    channel_counts = defaultdict(lambda: {"total": 0, "bad": 0})
    for order in orders:
        channel_counts[order.channel]["total"] += 1
        if order.state in ("cancelled", "refunded"):
            channel_counts[order.channel]["bad"] += 1

    for channel, counts in channel_counts.items():
        if counts["total"] >= 3 and counts["bad"] / counts["total"] >= 0.3:
            source_id = f"channel:{channel}"
            existing = Alert.query.filter_by(
                merchant_id=merchant_id, alert_type="fraud_risk", source_id=source_id, status="open"
            ).first()
            if not existing:
                a = Alert(
                    merchant_id=merchant_id,
                    alert_type="fraud_risk",
                    severity="warn",
                    title=f"High refund/cancellation rate on {channel}",
                    detail=f"{counts['bad']} of {counts['total']} recent {channel} orders were refunded or cancelled.",
                    source_id=source_id,
                    status="open",
                )
                db.session.add(a)
                created.append(a)
    db.session.commit()
    return created


def generate_ad_spend_alerts(merchant_id="merchant_shawn_01"):
    """Create alerts when ad spend is near budget."""
    rows = AdSpendAnalytic.query.filter_by(merchant_id=merchant_id).all()
    created = []
    for row in rows:
        if not row.budget_allocated or row.budget_allocated <= 0:
            continue
        pct = (row.current_spend or 0.0) / row.budget_allocated
        if pct >= 0.8:
            source_id = f"ad:{row.platform_source}"
            existing = Alert.query.filter_by(
                merchant_id=merchant_id, alert_type="ad_spend", source_id=source_id, status="open"
            ).first()
            if not existing:
                a = Alert(
                    merchant_id=merchant_id,
                    alert_type="ad_spend",
                    severity="warn" if pct < 1.0 else "crit",
                    title=f"{row.platform_source} spend at {pct*100:.0f}% of budget",
                    detail=f"Spent ${row.current_spend:.2f} of ${row.budget_allocated:.2f} budget.",
                    source_id=source_id,
                    status="open",
                )
                db.session.add(a)
                created.append(a)
    db.session.commit()
    return created


def refresh_alerts(merchant_id="merchant_shawn_01"):
    """Regenerate all alert types for a merchant."""
    generate_inventory_alerts(merchant_id)
    generate_fraud_alerts(merchant_id)
    generate_ad_spend_alerts(merchant_id)


def get_alerts(merchant_id, alert_type=None, limit=50):
    """Return open alerts for a merchant."""
    q = Alert.query.filter_by(merchant_id=merchant_id, status="open")
    if alert_type:
        q = q.filter_by(alert_type=alert_type)
    return q.order_by(Alert.created_at.desc()).limit(limit).all()


def get_fraud_alerts(merchant_id, limit=50):
    """Return open fraud/refund/cancellation alerts."""
    return Alert.query.filter(
        Alert.merchant_id == merchant_id,
        Alert.status == "open",
        Alert.alert_type.in_(["fraud_risk"]),
    ).order_by(Alert.created_at.desc()).limit(limit).all()


def alert_to_dict(alert, include_actions=True):
    data = {
        "id": alert.id,
        "level": alert.severity or "warn",
        "type": alert.alert_type,
        "severity": alert.severity,
        "title": alert.title,
        "detail": alert.detail,
        "source_id": alert.source_id,
        "status": alert.status,
        "dispatched_to": alert.dispatched_to,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "when": _relative_when(alert.created_at),
    }
    if include_actions:
        data["actions"] = ["Snooze", "Resolve"]
        if alert.alert_type == "inventory_runout":
            data["actions"].insert(0, "Create PO")
        elif alert.alert_type == "fraud_risk":
            data["actions"].insert(0, "Review order")
    return data


def fraud_alert_to_dict(alert):
    """Format an alert for the AI Fraud Detection template."""
    if alert.source_id.startswith("order:"):
        order = alert.source_id.split(":", 1)[1]
    elif alert.source_id.startswith("channel:"):
        order = alert.source_id.split(":", 1)[1]
    else:
        order = alert.source_id
    verdict = "block" if alert.severity == "crit" else "review"
    score = 85 if alert.severity == "crit" else 60
    return {
        "order": order,
        "verdict": verdict,
        "score": score,
        "reasons": alert.detail,
    }


def _dispatch_discord(alert):
    if not DISCORD_WEBHOOK_URL:
        return False
    try:
        import requests
        color = {"crit": 0xFF4444, "warn": 0xFFB020, "good": 0x22C55E}.get(alert.severity, 0x888888)
        payload = {
            "embeds": [{
                "title": alert.title,
                "description": alert.detail,
                "color": color,
                "fields": [{"name": "Severity", "value": alert.severity.upper(), "inline": True}],
            }]
        }
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        logger.error(f"[Discord dispatch] failed: {e}")
        return False


def _dispatch_sms(alert, to_number):
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, to_number]):
        return False
    try:
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        body = f"Vantav Alert: {alert.title} — {alert.detail[:80]}"
        message = client.messages.create(body=body, from_=TWILIO_FROM_NUMBER, to=to_number)
        return message.sid is not None
    except Exception as e:
        logger.error(f"[SMS dispatch] failed: {e}")
        return False


def dispatch_alert(alert, to_number=None):
    """Dispatch an alert to configured channels and record what succeeded."""
    dispatched = []
    if _dispatch_discord(alert):
        dispatched.append("discord")
    if to_number and _dispatch_sms(alert, to_number):
        dispatched.append("sms")
    alert.dispatched_to = json.dumps(dispatched)
    db.session.commit()
    return dispatched


def seed_demo_alerts(merchant_id="merchant_shawn_01"):
    """Seed a few demo alerts if none exist for the merchant."""
    if Alert.query.filter_by(merchant_id=merchant_id).first():
        return False
    demo = [
        Alert(merchant_id=merchant_id, alert_type="inventory_runout", severity="crit",
              title="Inventory will run out in 5 days",
              detail="Satin Sleep Set — 68 units left, selling 13/day. Supplier lead time is 6 days.",
              source_id="demo:1", status="open"),
        Alert(merchant_id=merchant_id, alert_type="fraud_risk", severity="warn",
              title="High refund rate on Shopify",
              detail="3 of 8 recent Shopify orders were refunded or cancelled.",
              source_id="demo:2", status="open"),
        Alert(merchant_id=merchant_id, alert_type="ad_spend", severity="warn",
              title="TikTok Video Ads spend at 85% of budget",
              detail="Spent $680.00 of $800.00 budget.",
              source_id="demo:3", status="open"),
    ]
    for a in demo:
        db.session.add(a)
    db.session.commit()
    logger.info(f"[ALERT MATRIX] Seeded demo alerts for {merchant_id}")
    return True
