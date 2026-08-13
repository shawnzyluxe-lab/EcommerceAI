"""Channel-level true-profit analytics."""
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any

from models import db, ProfitFeedOrder

logger = logging.getLogger(__name__)


def summarize_channels(merchant_id: str, days: int = 30) -> List[Dict[str, Any]]:
    """Return true-profit summary per sales channel for the trailing window."""
    since = datetime.utcnow() - timedelta(days=days)

    rows = (
        db.session.query(
            ProfitFeedOrder.channel.label("channel"),
            db.func.coalesce(db.func.sum(ProfitFeedOrder.gross_revenue), 0).label("revenue"),
            db.func.coalesce(db.func.sum(ProfitFeedOrder.cost_of_goods_sold), 0).label("cogs"),
            db.func.coalesce(db.func.sum(ProfitFeedOrder.marketplace_fees), 0).label("fees"),
            db.func.coalesce(db.func.sum(ProfitFeedOrder.shipping_costs), 0).label("shipping"),
            db.func.coalesce(db.func.sum(ProfitFeedOrder.refund_amount), 0).label("refunds"),
            db.func.coalesce(db.func.sum(ProfitFeedOrder.ad_spend_attributed), 0).label("ad_spend"),
            db.func.count(ProfitFeedOrder.id).label("orders"),
        )
        .filter(
            ProfitFeedOrder.merchant_id == merchant_id,
            ProfitFeedOrder.recorded_at >= since,
            ProfitFeedOrder.state != "cancelled",
        )
        .group_by(ProfitFeedOrder.channel)
        .order_by(db.func.sum(ProfitFeedOrder.gross_revenue).desc())
        .all()
    )

    results = []
    for r in rows:
        revenue = float(r.revenue or 0)
        cogs = float(r.cogs or 0)
        fees = float(r.fees or 0)
        shipping = float(r.shipping or 0)
        refunds = float(r.refunds or 0)
        ad_spend = float(r.ad_spend or 0)
        net = revenue - cogs - fees - shipping - refunds - ad_spend
        margin_pct = (net / revenue * 100) if revenue > 0 else 0.0
        results.append({
            "channel": r.channel,
            "revenue": round(revenue, 2),
            "cogs": round(cogs, 2),
            "fees": round(fees, 2),
            "shipping": round(shipping, 2),
            "refunds": round(refunds, 2),
            "ad_spend": round(ad_spend, 2),
            "net_profit": round(net, 2),
            "margin_pct": round(margin_pct, 2),
            "orders": int(r.orders or 0),
        })

    return results


def channel_totals(merchant_id: str, days: int = 30) -> Dict[str, Any]:
    """Return aggregate true-profit numbers across all channels."""
    channels = summarize_channels(merchant_id, days=days)
    totals = {
        "revenue": 0.0,
        "cogs": 0.0,
        "fees": 0.0,
        "shipping": 0.0,
        "refunds": 0.0,
        "ad_spend": 0.0,
        "net_profit": 0.0,
        "orders": 0,
    }
    for c in channels:
        for k in totals:
            totals[k] += c.get(k, 0) or 0
    for k in totals:
        if k != "orders":
            totals[k] = round(totals[k], 2)
        else:
            totals[k] = int(totals[k])
    totals["margin_pct"] = round((totals["net_profit"] / totals["revenue"] * 100), 2) if totals["revenue"] else 0.0
    return totals
