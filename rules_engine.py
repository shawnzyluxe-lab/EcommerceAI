"""Deterministic rule-based alerts pipeline.

Evaluates incoming multi-channel telemetry against the merchant's business_memory
guardrails and produces Alert + PendingAction drafts without calling external LLMs.
"""
import logging
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict

logger = logging.getLogger(__name__)

from models import db, BusinessMemory, Alert, Product, Supplier, UnifiedOrder, OrderItem, DailyCost
import action_gate
import forecaster


class BusinessMemoryProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_cac_threshold: float = 18.00
    floor_margin_percentage: int = 20
    max_daily_ad_spend: float = 500.00
    forbidden_discount_skus: List[str] = Field(default_factory=list)
    out_of_stock_buffer_days: int = 5
    refund_rate_ceiling: float = 0.15


class SKUTelemetry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sku: str
    revenue_24h: float = 0.0
    cogs_24h: float = 0.0
    ad_spend_24h: float = 0.0
    shipping_cost_24h: float = 0.0
    fees_24h: float = 0.0
    refunds_24h: float = 0.0
    taxes_24h: float = 0.0
    on_hand_inventory: int = 0
    daily_sales_velocity: float = 0.0
    refund_count_24h: int = 0
    total_orders_24h: int = 0
    lead_days: int = 14


def _to_float(v: Any) -> float:
    try:
        return float(Decimal(str(v or 0)))
    except Exception:
        return 0.0


def _memory_profile(merchant_id: str) -> BusinessMemoryProfile:
    """Load merchant guardrails from the business_memory table."""
    memory = BusinessMemory.query.filter_by(merchant_id=merchant_id).first()
    if not memory:
        memory = BusinessMemory(merchant_id=merchant_id)
        db.session.add(memory)
        db.session.commit()

    rules = memory.auto_escalation_rules or {}
    return BusinessMemoryProfile(
        max_cac_threshold=_to_float(memory.max_cac_threshold),
        floor_margin_percentage=int(memory.floor_margin_percentage or 20),
        max_daily_ad_spend=_to_float(memory.max_daily_ad_spend),
        forbidden_discount_skus=list(memory.forbidden_discount_skus or []),
        out_of_stock_buffer_days=int(rules.get("out_of_stock_buffer_days", 5)),
        refund_rate_ceiling=float(rules.get("refund_rate_ceiling", 0.15)),
    )


class VantaRulesEngine:
    """Deterministic diagnostic engine for margin, inventory runway, and refund spikes."""

    def __init__(self, memory: BusinessMemoryProfile):
        self.memory = memory

    def evaluate_sku_state(self, data: SKUTelemetry) -> List[Dict[str, Any]]:
        """Run the three diagnostic classes and return alert + action pairs."""
        generated_events: List[Dict[str, Any]] = []

        # Diagnostic Class A: Margin Compression
        total_costs = (
            data.cogs_24h
            + data.ad_spend_24h
            + data.shipping_cost_24h
            + data.fees_24h
            + data.refunds_24h
            + data.taxes_24h
        )
        net_profit = data.revenue_24h - total_costs
        current_margin_pct = (net_profit / data.revenue_24h * 100) if data.revenue_24h > 0 else 0.0

        if data.revenue_24h > 0 and current_margin_pct < self.memory.floor_margin_percentage:
            event_id = self._source_id(data.sku, "margin_compression")
            generated_events.append({
                "alert": {
                    "level": "crit",
                    "alert_type": "margin_compression",
                    "title": "Profit Margin Compression Detected",
                    "detail": (
                        f"SKU {data.sku} net margin is {current_margin_pct:.1f}%, "
                        f"below your configured {self.memory.floor_margin_percentage}% floor."
                    ),
                    "entity_ref": f"product://{data.sku}",
                    "source_id": event_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                "proposed_action": {
                    "action_type": "ad_adjust",
                    "title": f"Reduce ad spend for {data.sku}",
                    "detail": (
                        f"Heavy marketing overhead is dragging {data.sku} margin to {current_margin_pct:.1f}%. "
                        "Trim spend by 20% to restore profitability."
                    ),
                    "payload": {
                        "sku": data.sku,
                        "platform": "auto",
                        "strategy": "REDUCE_AD_SPEND",
                        "adjustment": -20.0,
                        "reason": "Compress marketing overhead to restore product net profitability margin parameters.",
                    },
                    "state": "draft",
                    "audit_id": event_id,
                },
            })

        # Diagnostic Class B: Inventory Stock Runway
        if data.daily_sales_velocity > 0:
            inventory_runway_days = data.on_hand_inventory / data.daily_sales_velocity
            days_until_reorder = inventory_runway_days - data.lead_days
            if days_until_reorder <= self.memory.out_of_stock_buffer_days:
                event_id = self._source_id(data.sku, "inventory_runout")
                generated_events.append({
                    "alert": {
                        "level": "crit",
                        "alert_type": "inventory_runout",
                        "title": "Inventory Stock Runway Failure",
                        "detail": (
                            f"SKU {data.sku} has {data.on_hand_inventory} units and is selling at "
                            f"{data.daily_sales_velocity:.1f}/day. Stockout in {inventory_runway_days:.1f} days "
                            f"with {data.lead_days}-day supplier lead time; buffer is {self.memory.out_of_stock_buffer_days} days."
                        ),
                        "entity_ref": f"product://{data.sku}",
                        "source_id": event_id,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                    "proposed_action": {
                        "action_type": "reorder",
                        "title": f"Reorder {data.sku} before stockout",
                        "detail": (
                            f"{data.sku} will stock out in {inventory_runway_days:.1f} days. "
                            "Place a reorder now to prevent lost sales."
                        ),
                        "payload": {
                            "sku": data.sku,
                            "quantity": max(int(data.daily_sales_velocity * (data.lead_days + self.memory.out_of_stock_buffer_days)), 100),
                            "supplier": "Supplier C",
                            "lead_days": data.lead_days,
                            "priority": "critical",
                            "reason": f"inventory runs out in {inventory_runway_days:.1f} days",
                        },
                        "state": "draft",
                        "audit_id": event_id,
                    },
                })

        # Diagnostic Class C: Refund Spike
        if data.total_orders_24h > 0:
            refund_ratio = data.refund_count_24h / data.total_orders_24h
            if refund_ratio >= self.memory.refund_rate_ceiling:
                event_id = self._source_id(data.sku, "refund_spike")
                generated_events.append({
                    "alert": {
                        "level": "warn",
                        "alert_type": "refund_spike",
                        "title": "Refund Filing Spike Exception",
                        "detail": (
                            f"SKU {data.sku} refund rate is {refund_ratio*100:.1f}% over the last 24h "
                            f"({data.refund_count_24h}/{data.total_orders_24h} orders), exceeding your "
                            f"{self.memory.refund_rate_ceiling*100:.1f}% ceiling."
                        ),
                        "entity_ref": f"product://{data.sku}",
                        "source_id": event_id,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                    "proposed_action": {
                        "action_type": "review_refunds",
                        "title": f"Review refunds for {data.sku}",
                        "detail": (
                            f"Refund spike on {data.sku} may indicate a supplier defect or listing issue. "
                            "Pause ads and investigate the root cause."
                        ),
                        "payload": {
                            "sku": data.sku,
                            "strategy": "ALERT_SUPPLIER_DEFECTS",
                            "reason": "Generate structural product variance inquiry reports to target manufacturer batch defects.",
                        },
                        "state": "draft",
                        "audit_id": event_id,
                    },
                })

        return generated_events

    def _source_id(self, sku: str, alert_type: str) -> str:
        """Deterministic source ID so the same condition in the same hour does not spam alerts."""
        bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        seed = f"{sku}:{alert_type}:{bucket}"
        return hashlib.sha256(seed.encode()).hexdigest()[:16]


def _on_hand_inventory(sku: str) -> int:
    """Look up on-hand inventory from the unified product catalog."""
    product = Product.query.filter_by(sku=sku).first()
    if product:
        return (product.on_hand or 0) + (product.inbound or 0)
    return 0


def get_sku_telemetry_from_unified(merchant_id: str, sku: str, window_hours: int = 24) -> SKUTelemetry:
    """Build a 24h SKU telemetry snapshot from the relational orders/order_items/daily_costs tables."""
    since_dt = datetime.utcnow() - timedelta(hours=window_hours)
    since_date = since_dt.date()

    line_rows = (
        db.session.query(
            db.func.coalesce(db.func.sum(OrderItem.qty * OrderItem.unit_price), 0).label("revenue"),
            db.func.coalesce(db.func.sum(OrderItem.qty * OrderItem.unit_cost), 0).label("cogs"),
        )
        .join(UnifiedOrder, UnifiedOrder.id == OrderItem.order_id)
        .filter(
            UnifiedOrder.merchant_id == merchant_id,
            OrderItem.sku == sku,
            UnifiedOrder.created_at >= since_dt,
        )
        .first()
    )

    daily = (
        db.session.query(
            db.func.coalesce(db.func.sum(DailyCost.ad_spend), 0).label("ad_spend"),
            db.func.coalesce(db.func.sum(DailyCost.ship_cost), 0).label("ship_cost"),
            db.func.coalesce(db.func.sum(DailyCost.fee), 0).label("fee"),
            db.func.coalesce(db.func.sum(DailyCost.refund), 0).label("refund"),
            db.func.coalesce(db.func.sum(DailyCost.tax), 0).label("tax"),
        )
        .filter(DailyCost.sku == sku, DailyCost.log_date >= since_date)
        .first()
    )

    order_counts = (
        db.session.query(
            db.func.count(UnifiedOrder.id).label("total"),
            db.func.count(db.case((UnifiedOrder.status.in_(["refunded", "cancelled"]), 1))).label("refund_count"),
        )
        .join(OrderItem, UnifiedOrder.id == OrderItem.order_id)
        .filter(
            UnifiedOrder.merchant_id == merchant_id,
            OrderItem.sku == sku,
            UnifiedOrder.created_at >= since_dt,
        )
        .first()
    )

    forecast = forecaster.forecast_sku(merchant_id, sku, days=max(window_hours // 24, 14))

    product = Product.query.filter_by(sku=sku).first()
    supplier = None
    if product and product.supplier_id:
        supplier = Supplier.query.filter_by(id=product.supplier_id).first()
    lead_days = supplier.lead_days if supplier else 14

    return SKUTelemetry(
        sku=sku,
        revenue_24h=round(float(line_rows.revenue or 0), 2),
        cogs_24h=round(float(line_rows.cogs or 0), 2),
        ad_spend_24h=round(float(daily.ad_spend or 0), 2),
        shipping_cost_24h=round(float(daily.ship_cost or 0), 2),
        fees_24h=round(float(daily.fee or 0), 2),
        refunds_24h=round(float(daily.refund or 0), 2),
        taxes_24h=round(float(daily.tax or 0), 2),
        on_hand_inventory=_on_hand_inventory(sku),
        daily_sales_velocity=forecast.predicted_daily_velocity,
        refund_count_24h=int(order_counts.refund_count or 0),
        total_orders_24h=int(order_counts.total or 0),
        lead_days=lead_days,
    )


def evaluate_products(merchant_id: str, window_hours: int = 24) -> List[Dict[str, Any]]:
    """Run rules across every product in the merchant catalog."""
    memory = _memory_profile(merchant_id)
    engine = VantaRulesEngine(memory)
    products = Product.query.filter_by(merchant_id=merchant_id).all()

    all_events: List[Dict[str, Any]] = []
    for product in products:
        telemetry = get_sku_telemetry_from_unified(merchant_id, product.sku, window_hours=window_hours)
        all_events.extend(engine.evaluate_sku_state(telemetry))
    return save_events(merchant_id, all_events)


def save_events(merchant_id: str, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Persist generated alert/action pairs, skipping duplicates."""
    created: List[Dict[str, Any]] = []
    for event in events:
        alert_data = event.get("alert") or {}
        action_data = event.get("proposed_action") or {}
        source_id = alert_data.get("source_id") or action_data.get("audit_id")
        alert_type = alert_data.get("alert_type") or "general"

        existing = Alert.query.filter_by(
            merchant_id=merchant_id,
            source_id=source_id,
            status="open",
        ).first()
        if existing:
            continue

        alert = Alert(
            merchant_id=merchant_id,
            alert_type=alert_type,
            severity=alert_data.get("level", "warn"),
            title=alert_data.get("title", "Alert"),
            detail=alert_data.get("detail", ""),
            source_id=source_id or str(uuid.uuid4())[:16],
            status="open",
        )
        db.session.add(alert)
        db.session.flush()

        payload = dict(action_data.get("payload") or {})
        payload["audit_id"] = source_id
        payload["entity_ref"] = alert_data.get("entity_ref", "")

        try:
            action_gate.create_action(
                merchant_id=merchant_id,
                action_type=action_data.get("action_type", "review"),
                title=action_data.get("title", "Review alert"),
                detail=action_data.get("detail", ""),
                payload=payload,
                alert_id=alert.id,
            )
        except Exception as e:
            logger.warning(f"[Rules Engine] action_gate.create_action rejected {action_data.get('action_type')}: {e}")
            db.session.rollback()
            continue

        created.append({
            "alert_id": alert.id,
            "alert_type": alert.alert_type,
            "title": alert.title,
            "action_type": action_data.get("action_type"),
        })

    return created


def run_for_merchant(merchant_id: str, window_hours: int = 24) -> List[Dict[str, Any]]:
    """Evaluate all SKU-level telemetry for a merchant and write alerts/actions."""
    return evaluate_products(merchant_id, window_hours=window_hours)


def run_for_sku(merchant_id: str, telemetry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Evaluate explicit SKU telemetry (e.g. from an ingestion broker)."""
    memory = _memory_profile(merchant_id)
    engine = VantaRulesEngine(memory)
    data = SKUTelemetry(**telemetry)
    events = engine.evaluate_sku_state(data)
    return save_events(merchant_id, events)


if __name__ == "__main__":
    mock_memory = BusinessMemoryProfile(
        max_cac_threshold=18.00,
        floor_margin_percentage=25,
        out_of_stock_buffer_days=5,
        refund_rate_ceiling=0.15,
    )
    engine = VantaRulesEngine(mock_memory)
    sample = SKUTelemetry(
        sku="SKU-404-PODS",
        revenue_24h=1000.00,
        cogs_24h=400.00,
        ad_spend_24h=350.00,
        shipping_cost_24h=120.00,
        fees_24h=50.00,
        refunds_24h=0.00,
        taxes_24h=30.00,
        on_hand_inventory=8,
        daily_sales_velocity=4.0,
        refund_count_24h=4,
        total_orders_24h=20,
    )
    anomalies = engine.evaluate_sku_state(sample)
    print(f"--- FOUND {len(anomalies)} ANOMALIES ---")
    for event in anomalies:
        print(
            f"[{event['alert']['level'].upper()}] {event['alert']['title']} "
            f"-> {event['proposed_action']['action_type']}"
        )
