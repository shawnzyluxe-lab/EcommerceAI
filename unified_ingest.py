"""Ingestion helpers that write into the unified products/orders/order_items tables.

Keeps the existing ProfitFeedOrder path intact while populating the relational
schema that powers the forecaster and SKU-level rule engine.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from models import db, Product, Supplier, UnifiedOrder, OrderItem, DailyCost

logger = logging.getLogger(__name__)


def _to_float(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _to_int(v: Any) -> int:
    try:
        return int(v or 0)
    except Exception:
        return 0


def upsert_supplier(merchant_id: str, name: str = "Default Supplier", lead_days: int = 14) -> Supplier:
    supplier = Supplier.query.filter_by(merchant_id=merchant_id, name=name).first()
    if not supplier:
        supplier = Supplier(
            merchant_id=merchant_id,
            name=name,
            lead_days=lead_days,
        )
        db.session.add(supplier)
        db.session.flush()
    return supplier


def upsert_product(
    merchant_id: str,
    sku: str,
    title: str = "",
    unit_cost: Optional[float] = None,
    unit_price: Optional[float] = None,
    on_hand: int = 0,
    supplier_id: Optional[str] = None,
    channel: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> Product:
    product = Product.query.filter_by(sku=sku).first()
    if product and product.merchant_id != merchant_id:
        # SKU collision across merchants is not supported in the current schema.
        logger.warning(f"[unified_ingest] SKU {sku} belongs to another merchant; skipping")
        return product

    derived_cost = unit_cost if unit_cost is not None else (unit_price * 0.35 if unit_price else 0.0)
    if not product:
        product = Product(
            sku=sku,
            merchant_id=merchant_id,
            title=title or sku,
            unit_cost=derived_cost,
            on_hand=on_hand or 100,
            supplier_id=supplier_id,
            channel_ids={} if not channel else {channel: channel_id or ""},
        )
        db.session.add(product)
    else:
        if title and not product.title:
            product.title = title
        if derived_cost and (product.unit_cost is None or float(product.unit_cost) == 0):
            product.unit_cost = derived_cost
        if supplier_id and not product.supplier_id:
            product.supplier_id = supplier_id
        if channel:
            channel_ids = product.channel_ids or {}
            if channel_id and channel not in channel_ids:
                channel_ids[channel] = channel_id
            product.channel_ids = channel_ids
    db.session.flush()
    return product


def record_unified_order(
    merchant_id: str,
    channel: str,
    order_id: str,
    order_items: List[Dict[str, Any]],
    revenue: Optional[float] = None,
    status: str = "shipped",
    customer_id: Optional[str] = None,
    ship_to: Optional[Dict[str, Any]] = None,
    created_at: Optional[datetime] = None,
    default_supplier_id: Optional[str] = None,
    shipping_cost: float = 0.0,
    fee: float = 0.0,
    refund: float = 0.0,
    tax: float = 0.0,
) -> Optional[UnifiedOrder]:
    """Persist a unified order with line items, creating/updating products as needed."""
    if not order_id or not order_items:
        return None

    # Normalize line items
    normalized = []
    for item in order_items:
        sku = (item.get("sku") or item.get("product_id") or "").strip()
        if not sku:
            continue
        qty = _to_int(item.get("quantity") or item.get("qty"))
        if qty <= 0:
            qty = 1
        unit_price = _to_float(item.get("price") or item.get("unit_price"))
        unit_cost = _to_float(item.get("unit_cost") or item.get("cost"))
        title = item.get("title") or item.get("name") or ""
        normalized.append({
            "sku": sku,
            "qty": qty,
            "unit_price": unit_price,
            "unit_cost": unit_cost,
            "title": title,
        })

    if not normalized:
        return None

    order = UnifiedOrder.query.filter_by(id=order_id).first()
    if order:
        # Idempotent: skip if already ingested.
        return order

    if created_at is None:
        created_at = datetime.now(timezone.utc)
    if revenue is None:
        revenue = sum(i["unit_price"] * i["qty"] for i in normalized)

    order = UnifiedOrder(
        id=order_id,
        merchant_id=merchant_id,
        channel=channel,
        revenue=round(revenue, 4),
        status=status,
        customer_id=customer_id,
        ship_to=ship_to or {},
        created_at=created_at,
    )
    db.session.add(order)
    db.session.flush()

    total_line_revenue = sum(i["unit_price"] * i["qty"] for i in normalized)
    allocation_base = total_line_revenue or revenue or 1.0

    for item in normalized:
        product = upsert_product(
            merchant_id=merchant_id,
            sku=item["sku"],
            title=item["title"],
            unit_cost=item["unit_cost"] if item["unit_cost"] else None,
            unit_price=item["unit_price"] if item["unit_price"] else None,
            supplier_id=default_supplier_id,
            channel=channel,
        )
        unit_cost = item["unit_cost"] if item["unit_cost"] else float(product.unit_cost or 0)
        if not unit_cost and item["unit_price"]:
            unit_cost = item["unit_price"] * 0.35
        oi = OrderItem(
            order_id=order.id,
            sku=item["sku"],
            qty=item["qty"],
            unit_price=round(item["unit_price"], 4),
            unit_cost=round(unit_cost, 4),
        )
        db.session.add(oi)

        # Allocate order-level costs to SKUs proportionally by line revenue.
        line_revenue = item["unit_price"] * item["qty"]
        share = line_revenue / allocation_base if allocation_base else 0.0
        log_date = created_at.date() if created_at else datetime.now(timezone.utc).date()
        record_daily_cost(
            merchant_id=merchant_id,
            sku=item["sku"],
            log_date=log_date,
            ship_cost=shipping_cost * share,
            fee=fee * share,
            refund=refund * share if status in ("refunded", "cancelled") else 0.0,
            tax=tax * share,
        )

    return order


def record_daily_cost(
    merchant_id: str,
    sku: str,
    log_date: datetime.date,
    ad_spend: float = 0.0,
    ship_cost: float = 0.0,
    fee: float = 0.0,
    refund: float = 0.0,
    tax: float = 0.0,
) -> None:
    """Upsert a daily cost row for an SKU. If product missing, create a stub."""
    upsert_product(merchant_id, sku)
    row = DailyCost.query.filter_by(sku=sku, log_date=log_date).first()
    if not row:
        row = DailyCost(
            sku=sku,
            log_date=log_date,
            ad_spend=ad_spend,
            ship_cost=ship_cost,
            fee=fee,
            refund=refund,
            tax=tax,
        )
        db.session.add(row)
    else:
        row.ad_spend = float(row.ad_spend or 0) + ad_spend
        row.ship_cost = float(row.ship_cost or 0) + ship_cost
        row.fee = float(row.fee or 0) + fee
        row.refund = float(row.refund or 0) + refund
        row.tax = float(row.tax or 0) + tax
