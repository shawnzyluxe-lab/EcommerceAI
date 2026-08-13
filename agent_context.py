"""Live merchant data context for the assistant."""
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List

import profit_feed
import alert_matrix
import action_gate
import channels as channels_module
import tiktok_studio
from models import MerchantProfile, MerchantSetting, PendingAction, db


def _get_products(merchant_id: str) -> List[Dict[str, Any]]:
    products: List[Dict[str, Any]] = []
    for key, channel in [
        ("shopify_products", "shopify"),
        ("tiktok_products", "tiktok"),
        ("amazon_products", "amazon"),
    ]:
        setting = MerchantSetting.query.filter_by(merchant_id=merchant_id, setting_key=key).first()
        if setting and setting.setting_value:
            try:
                items = json.loads(setting.setting_value)
                if isinstance(items, list):
                    for p in items[:10]:
                        p["channel"] = channel
                        products.append(p)
            except json.JSONDecodeError:
                pass
    return products


def _get_channels(merchant_id: str) -> List[Dict[str, Any]]:
    try:
        return channels_module.list_channels(merchant_id)
    except Exception:
        return []


def get_snapshot(merchant_id: str) -> Dict[str, Any]:
    """Build a structured snapshot of the merchant's current state."""
    profile = MerchantProfile.query.get(merchant_id)
    kpis = profit_feed.get_kpis(merchant_id)
    breakdown = profit_feed.get_profit_breakdown(merchant_id)
    recent_orders = profit_feed.get_recent_orders(merchant_id, limit=20)
    alerts = alert_matrix.get_alerts(merchant_id, limit=10)
    # Query pending actions directly to avoid recursive refresh when Action Gate is building evidence.
    actions = PendingAction.query.filter_by(
        merchant_id=merchant_id, status="pending"
    ).order_by(PendingAction.created_at.desc()).limit(10).all()
    channels = _get_channels(merchant_id)
    products = _get_products(merchant_id)
    tiktok_state = tiktok_studio.get_state(merchant_id)

    return {
        "merchant_id": merchant_id,
        "business_name": profile.business_name if profile else "Unknown",
        "tier": profile.account_tier if profile else "Basic Tier",
        "sandbox_status": profile.sandbox_status if profile else "pending",
        "kpis": kpis,
        "profit_breakdown": breakdown,
        "recent_orders": recent_orders,
        "alerts": [alert_matrix.alert_to_dict(a) for a in alerts],
        "pending_actions": [action_gate.action_to_dict(a) for a in actions],
        "channels": channels,
        "products": products,
        "tiktok_studio": tiktok_state,
        "timestamp": datetime.utcnow().isoformat(),
    }


def format_snapshot(snapshot: Dict[str, Any], max_lines: int = 80) -> str:
    """Render the snapshot into a concise system context string."""
    parts = []
    parts.append(f"Merchant: {snapshot['business_name']} ({snapshot['merchant_id']})")
    parts.append(f"Tier: {snapshot['tier']} | Sandbox: {snapshot['sandbox_status']}")

    kpis = snapshot.get("kpis") or {}
    def _fmt(val, unit=""):
        return f"{val}{unit}" if val is not None else "—"
    parts.append(
        f"KPIs: gross=${_fmt(kpis.get('gross_revenue'))} net=${_fmt(kpis.get('net_profit'))} "
        f"margin={_fmt(kpis.get('net_margin'))}% orders={_fmt(kpis.get('orders'))}"
    )

    channels = snapshot.get("channels") or []
    if channels:
        parts.append("Channels: " + ", ".join(
            f"{c.get('name','?')} ({c.get('state','?')}, {c.get('orders',0)} orders)" for c in channels
        ))

    orders = snapshot.get("recent_orders") or []
    if orders:
        parts.append(f"Recent orders ({len(orders)}): " + "; ".join(
            f"{o.get('order_number') or o.get('id','?')} {o.get('channel','?')} ${o.get('gross_revenue','?')} net={o.get('net_profit','?')}"
            for o in orders[:5]
        ))

    alerts = snapshot.get("alerts") or []
    if alerts:
        parts.append("Alerts: " + "; ".join(f"{a.get('title','')} ({a.get('severity','')})" for a in alerts[:5]))

    actions = snapshot.get("pending_actions") or []
    if actions:
        parts.append("Pending actions: " + "; ".join(
            f"{a.get('title','')} [{a.get('action_type','')}]" for a in actions[:5]
        ))

    products = snapshot.get("products") or []
    if products:
        parts.append("Products: " + "; ".join(
            f"{p.get('title','?')} {p.get('sku','?')} ${p.get('price','?')} x{p.get('inventory_quantity',0)}"
            for p in products[:5]
        ))

    return "\n".join(parts[:max_lines])


def search_merchant_data(merchant_id: str, query: str) -> str:
    """Keyword-driven retrieval of relevant merchant snippets for RAG-style answers."""
    query_words = [w.lower() for w in query.split() if len(w) > 2]
    snap = get_snapshot(merchant_id)
    results: List[str] = []

    # Search products
    for p in snap.get("products", []):
        text = " ".join(str(v) for v in p.values() if isinstance(v, str)).lower()
        if any(w in text for w in query_words):
            results.append(
                f"Product: {p.get('title','?')} SKU {p.get('sku','?')} "
                f"price ${p.get('price','?')} inventory {p.get('inventory_quantity',0)} channel {p.get('channel','?')}"
            )

    # Search orders
    for o in snap.get("recent_orders", []):
        text = " ".join(str(v) for v in o.values() if isinstance(v, str)).lower()
        if any(w in text for w in query_words):
            results.append(
                f"Order: {o.get('order_number') or o.get('id','?')} channel {o.get('channel','?')} "
                f"gross ${o.get('gross_revenue','?')} net {o.get('net_profit','?')} state {o.get('state','?')}"
            )

    # Search alerts
    for a in snap.get("alerts", []):
        text = f"{a.get('title','')} {a.get('body','')}".lower()
        if any(w in text for w in query_words):
            results.append(f"Alert: {a.get('title','')} — {a.get('body','')}")

    if not results:
        return "No direct matches found. Use the available tools for a full report."
    return "\n".join(results[:10])
