"""Real-time Profit Feed for Prometheus OS.

Aggregates channel orders and ad spend into a true-profit view.
"""
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import tracking
import rules_engine
import unified_ingest
from models import db, ProfitFeedOrder, AdSpendFeed, Product, OrderItem

logger = logging.getLogger(__name__)

# Default commercial assumptions per channel for beta merchants.
# These can be overridden per-merchant via MerchantSetting later.
CHANNEL_DEFAULTS = {
    "shopify":    {"cogs_pct": 0.35, "fee_pct": 0.029, "fee_fixed": 0.30, "shipping": 5.00},
    "tiktok":     {"cogs_pct": 0.35, "fee_pct": 0.050, "fee_fixed": 0.00, "shipping": 4.00},
    "amazon":     {"cogs_pct": 0.35, "fee_pct": 0.150, "fee_fixed": 0.00, "shipping": 6.00},
    "etsy":       {"cogs_pct": 0.35, "fee_pct": 0.065, "fee_fixed": 0.20, "shipping": 4.00},
    "ebay":       {"cogs_pct": 0.35, "fee_pct": 0.130, "fee_fixed": 0.00, "shipping": 5.00},
    "woocommerce":{"cogs_pct": 0.35, "fee_pct": 0.029, "fee_fixed": 0.30, "shipping": 5.00},
    "walmart":    {"cogs_pct": 0.35, "fee_pct": 0.150, "fee_fixed": 0.00, "shipping": 6.00},
    "bigcommerce":{"cogs_pct": 0.35, "fee_pct": 0.029, "fee_fixed": 0.30, "shipping": 5.00},
}

# Ad platforms that don't map 1:1 to a sales channel are attributed to Shopify
# by default for the beta; this can be made configurable later.
AD_PLATFORM_CHANNEL = {
    "meta": "shopify",
    "facebook": "shopify",
    "google": "shopify",
    "shopify": "shopify",
    "tiktok": "tiktok",
    "amazon": "amazon",
    "etsy": "etsy",
    "ebay": "ebay",
    "woocommerce": "woocommerce",
    "walmart": "walmart",
    "bigcommerce": "bigcommerce",
}


def _channel_defaults(channel):
    return CHANNEL_DEFAULTS.get(channel, CHANNEL_DEFAULTS["shopify"])


def _normalize_channel(channel):
    return (channel or "").lower().replace(" shop", "").replace(" shop", "").replace(" marketplace", "").strip()


def record_order(merchant_id, channel, order_id, gross_revenue, items=1, state="shipped", refund_amount=0.0, tracking_number="", carrier="", order_items=None):
    """Record a channel order and compute its true net profit.

    Returns the created/updated ProfitFeedOrder or None if already recorded.
    """
    channel = _normalize_channel(channel)
    if not order_id:
        logger.warning("[PROFIT FEED] Ignored order with no order_id")
        return None

    existing = ProfitFeedOrder.query.filter_by(merchant_id=merchant_id, order_id=order_id).first()
    if existing:
        # Idempotent update for state/refund/tracking changes from webhooks.
        existing.state = state
        existing.refund_amount = float(refund_amount or 0.0)
        if tracking_number:
            existing.tracking_number = tracking.normalize_tracking_number(tracking_number)
            existing.carrier = (carrier or tracking.detect_carrier(existing.tracking_number)).lower()
        existing.net_profit = _compute_net(existing, existing.ad_spend_attributed)
        db.session.commit()
        return existing

    defaults = _channel_defaults(channel)
    gross = float(gross_revenue or 0.0)
    fees = gross * defaults["fee_pct"] + defaults["fee_fixed"]
    cogs = _compute_order_cogs(merchant_id, order_items, defaults, gross)
    shipping = defaults["shipping"] * max(int(items or 1), 1)
    refund = float(refund_amount or 0.0)

    clean_tracking = tracking.normalize_tracking_number(tracking_number or "")
    detected_carrier = (carrier or tracking.detect_carrier(clean_tracking) or "").lower()
    order = ProfitFeedOrder(
        merchant_id=merchant_id,
        order_id=order_id,
        channel=channel,
        items=int(items or 1),
        gross_revenue=gross,
        marketplace_fees=round(fees, 2),
        cost_of_goods_sold=round(cogs, 2),
        shipping_costs=round(shipping, 2),
        ad_spend_attributed=0.0,
        refund_amount=round(refund, 2),
        state=state,
        tracking_number=clean_tracking,
        carrier=detected_carrier,
    )
    order.net_profit = _compute_net(order, 0.0)
    db.session.add(order)
    if order_items:
        try:
            unified_ingest.record_unified_order(
                merchant_id=merchant_id,
                channel=channel,
                order_id=order_id,
                order_items=order_items,
                revenue=gross,
                status=state,
                created_at=datetime.now(timezone.utc),
                shipping_cost=shipping,
                fee=fees,
                refund=refund,
                tax=0.0,
            )
        except Exception as e:
            logger.warning(f"[UNIFIED INGEST] record_unified_order failed: {e}")
    db.session.commit()
    try:
        rules_engine.run_for_merchant(merchant_id)
    except Exception as e:
        logger.warning(f"[RULES ENGINE] record_order run failed: {e}")
    return order


def _compute_item_cogs(merchant_id, item, defaults):
    """Return COGS for one line item using the product's unit cost when available."""
    sku = str(item.get("sku") or item.get("product_id") or "").strip()
    qty = int(item.get("qty") or item.get("quantity") or 1)
    unit_price = float(item.get("price") or item.get("unit_price") or 0.0)
    if sku and merchant_id:
        product = Product.query.filter_by(sku=sku, merchant_id=merchant_id).first()
        if product:
            unit_cost = float(product.unit_cost or 0)
            if unit_cost > 0:
                return unit_cost * qty
    if unit_price > 0:
        return unit_price * qty * defaults.get("cogs_pct", 0.35)
    return 0.0


def _compute_order_cogs(merchant_id, order_items, defaults, gross_fallback=0.0):
    """Sum per-line COGS using product unit cost when possible, else channel estimate."""
    if order_items:
        return round(
            sum(
                _compute_item_cogs(merchant_id, item, defaults)
                for item in order_items
                if isinstance(item, dict)
            ),
            2,
        )
    return round(gross_fallback * defaults.get("cogs_pct", 0.35), 2)


def _compute_net(order, ad_spend):
    return round(
        order.gross_revenue
        - order.marketplace_fees
        - order.cost_of_goods_sold
        - order.shipping_costs
        - ad_spend
        - order.refund_amount,
        2,
    )


def record_ad_spend(merchant_id, platform, amount, conversion_count=0):
    """Record ad spend for a platform. Returns the created row."""
    spend = AdSpendFeed(
        merchant_id=merchant_id,
        platform_source=(platform or "").lower(),
        amount=float(amount or 0.0),
        conversion_count=int(conversion_count or 0),
    )
    db.session.add(spend)
    db.session.commit()
    try:
        rules_engine.run_for_merchant(merchant_id)
    except Exception as e:
        logger.warning(f"[RULES ENGINE] record_ad_spend run failed: {e}")
    return spend


def _channel_ad_spend(merchant_id, since=None):
    """Return a dict of ad spend attributed to each sales channel."""
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=1)
    rows = AdSpendFeed.query.filter(
        AdSpendFeed.merchant_id == merchant_id,
        AdSpendFeed.recorded_at >= since,
    ).all()

    channel_spend = defaultdict(float)
    for r in rows:
        ch = AD_PLATFORM_CHANNEL.get(r.platform_source, "shopify")
        channel_spend[ch] += float(r.amount or 0.0)

    # Cross-platform spend (meta/google) with no target channel gets distributed
    # to all active sales channels by order count if Shopify has no orders.
    cross = channel_spend.get("cross", 0.0)
    if cross:
        del channel_spend["cross"]
    return dict(channel_spend)


def _orders_with_ad_attribution(merchant_id, limit=50, since=None):
    """Return order dicts with ad spend attributed per channel at query time."""
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=30)
    orders = ProfitFeedOrder.query.filter(
        ProfitFeedOrder.merchant_id == merchant_id,
        ProfitFeedOrder.recorded_at >= since,
    ).order_by(ProfitFeedOrder.recorded_at.desc()).limit(limit).all()

    if not orders:
        return []

    channel_spend = _channel_ad_spend(merchant_id, since=since - timedelta(days=1))
    order_counts = defaultdict(int)
    for o in orders:
        order_counts[o.channel] += 1

    result = []
    for o in orders:
        spend_per_order = channel_spend.get(o.channel, 0.0) / max(order_counts[o.channel], 1)
        net = _compute_net(o, spend_per_order)
        margin = round(net / o.gross_revenue * 100, 1) if o.gross_revenue else 0.0
        order_id = o.order_id or ""
        order_number = order_id.split(":")[-1] if ":" in order_id else order_id
        result.append({
            "id": order_id,
            "order_number": order_number,
            "channel": o.channel.title() if o.channel else o.channel,
            "items": o.items,
            "revenue": float(o.gross_revenue),
            "profit": net,
            "margin": margin,
            "state": o.state,
            "tracking_number": o.tracking_number or "",
            "carrier": o.carrier or "",
            "tracking_url": tracking.tracking_url(o.tracking_number or "", o.carrier or ""),
            "recorded_at": o.recorded_at.isoformat() if o.recorded_at else None,
        })
    return result


def get_recent_orders(merchant_id, limit=50):
    """Recent orders for the Profit Engine table."""
    return _orders_with_ad_attribution(merchant_id, limit=limit)


def get_profit_breakdown(merchant_id, window_days=30):
    """Return profit feed line items and totals for the dashboard/API."""
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    orders = ProfitFeedOrder.query.filter(
        ProfitFeedOrder.merchant_id == merchant_id,
        ProfitFeedOrder.recorded_at >= since,
    ).all()

    gross = 0.0
    product_cost = 0.0
    marketplace_fees = 0.0
    shipping = 0.0
    refunds = 0.0
    channel_order_counts = defaultdict(int)
    for o in orders:
        gross += float(o.gross_revenue)
        product_cost += float(o.cost_of_goods_sold)
        marketplace_fees += float(o.marketplace_fees)
        shipping += float(o.shipping_costs)
        refunds += float(o.refund_amount)
        channel_order_counts[o.channel] += 1

    channel_spend = _channel_ad_spend(merchant_id, since=since)
    # Attribute ad spend using the same per-order logic as _orders_with_ad_attribution
    ad_spend = 0.0
    for channel, total_spend in channel_spend.items():
        ad_spend += total_spend

    net = gross - product_cost - marketplace_fees - shipping - refunds - ad_spend
    margin = round(net / gross * 100, 1) if gross else 0.0

    rows = [
        {"label": "Gross revenue", "amount": round(gross, 2), "kind": "in"},
        {"label": "Product cost", "amount": -round(product_cost, 2), "kind": "out"},
        {"label": "Ad spend", "amount": -round(ad_spend, 2), "kind": "out"},
        {"label": "Marketplace fees", "amount": -round(marketplace_fees, 2), "kind": "out"},
        {"label": "Shipping", "amount": -round(shipping, 2), "kind": "out"},
        {"label": "Refunds", "amount": -round(refunds, 2), "kind": "out"},
    ]

    return {
        "profit_rows": rows,
        "gross_revenue": round(gross, 2),
        "total_costs": round(product_cost + marketplace_fees + shipping + refunds + ad_spend, 2),
        "net_profit": round(net, 2),
        "net_margin": margin,
    }


def get_kpis(merchant_id, window_days=1):
    """High-level profit KPIs for the Profit Feed. Gross and net share one window."""
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    window_orders = ProfitFeedOrder.query.filter(
        ProfitFeedOrder.merchant_id == merchant_id,
        ProfitFeedOrder.recorded_at >= since,
    ).all()

    orders = len(window_orders)

    breakdown = get_profit_breakdown(merchant_id, window_days=window_days)
    gross = breakdown["gross_revenue"]
    net = breakdown["net_profit"]
    margin = breakdown["net_margin"]
    aov = round(gross / orders, 2) if orders else 0.0

    channel_spend = _channel_ad_spend(merchant_id, since=since)
    ad_spend = sum(channel_spend.values())

    return {
        "merchant_id": merchant_id,
        "window": f"{window_days * 24}h" if window_days < 2 else f"{window_days}d",
        "gross_revenue": round(gross, 2),
        "net_profit": round(net, 2),
        "net_margin": margin,
        "orders": orders,
        "aov": aov,
        "ad_spend": round(ad_spend, 2),
        "channels": channel_spend,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def recalc_order_cogs_from_items(merchant_id, order_id, order_items=None):
    """Recompute a ProfitFeedOrder's COGS/net from line items and current Product.unit_cost.

    If order_items is supplied, it should be a list of dicts with sku/qty/price.
    Otherwise, the function loads OrderItem rows for the order from the database.
    """
    pf = ProfitFeedOrder.query.filter_by(merchant_id=merchant_id, order_id=order_id).first()
    if not pf:
        return None

    if order_items is None:
        order_items = [
            {
                "sku": oi.sku,
                "qty": oi.qty,
                "price": float(oi.unit_price or 0),
            }
            for oi in OrderItem.query.filter_by(order_id=order_id).all()
            if oi.sku
        ]

    defaults = _channel_defaults(pf.channel)
    pf.cost_of_goods_sold = _compute_order_cogs(merchant_id, order_items, defaults, pf.gross_revenue)
    pf.net_profit = _compute_net(pf, float(pf.ad_spend_attributed or 0.0))
    db.session.add(pf)
    return pf


def recalc_profit_for_sku(merchant_id, sku):
    """Recompute all profit feed orders that contain the given SKU."""
    order_ids = (
        db.session.query(ProfitFeedOrder.order_id)
        .join(OrderItem, OrderItem.order_id == ProfitFeedOrder.order_id)
        .filter(ProfitFeedOrder.merchant_id == merchant_id, OrderItem.sku == sku)
        .distinct()
        .all()
    )
    updated = 0
    for (order_id,) in order_ids:
        if recalc_order_cogs_from_items(merchant_id, order_id):
            updated += 1
    db.session.commit()
    return updated


def recalc_profit_for_merchant(merchant_id):
    """Recompute all profit feed orders for a merchant."""
    order_ids = (
        db.session.query(ProfitFeedOrder.order_id)
        .filter_by(merchant_id=merchant_id)
        .distinct()
        .all()
    )
    updated = 0
    for (order_id,) in order_ids:
        if recalc_order_cogs_from_items(merchant_id, order_id):
            updated += 1
    db.session.commit()
    return updated


def seed_demo_data(merchant_id="merchant_shawn_01"):
    """Create sample profit feed data if the merchant has no orders."""
    if ProfitFeedOrder.query.filter_by(merchant_id=merchant_id).first():
        return False

    samples = [
        ("#1042", "shopify", 2, 128.00, "delayed"),
        ("#1041", "tiktok", 1, 64.00, "shipped"),
        ("#1040", "shopify", 3, 214.50, "shipped"),
        ("#1039", "amazon", 1, 82.00, "packed"),
        ("#1038", "etsy", 2, 96.40, "delayed"),
        ("#1037", "shopify", 1, 58.00, "refunded"),
    ]
    for order_id, channel, items, gross, state in samples:
        record_order(merchant_id, channel, order_id, gross, items=items, state=state)

    # Seed ad spend for the demo merchant.
    if not AdSpendFeed.query.filter_by(merchant_id=merchant_id).first():
        record_ad_spend(merchant_id, "meta", 80.00, 24)
        record_ad_spend(merchant_id, "tiktok", 120.00, 38)
        record_ad_spend(merchant_id, "amazon", 40.00, 7)

    logger.info(f"[PROFIT FEED] Seeded demo data for {merchant_id}")
    return True
