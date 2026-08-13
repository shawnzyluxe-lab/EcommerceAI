"""Deterministic rule-based alerts pipeline.

Evaluates incoming multi-channel telemetry against the merchant's business_memory
guardrails and produces Alert + PendingAction drafts without calling external LLMs.
"""
import json
import logging
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict

logger = logging.getLogger(__name__)

from models import db, BusinessMemory, Alert, PredictiveLogistics, ProfitFeedOrder, AdSpendFeed, LocalProductCatalog
import action_gate


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
            if 0 < inventory_runway_days <= self.memory.out_of_stock_buffer_days:
                event_id = self._source_id(data.sku, "inventory_runout")
                generated_events.append({
                    "alert": {
                        "level": "crit",
                        "alert_type": "inventory_runout",
                        "title": "Inventory Stock Runway Failure",
                        "detail": (
                            f"SKU {data.sku} has {data.on_hand_inventory} units and is selling at "
                            f"{data.daily_sales_velocity:.1f}/day. Runway is {inventory_runway_days:.1f} days, "
                            f"below your {self.memory.out_of_stock_buffer_days}-day buffer."
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
                            "quantity": max(int(data.daily_sales_velocity * 30), 100),
                            "supplier": "Supplier C",
                            "lead_days": 6,
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


def _on_hand_inventory(merchant_id: str, sku: str) -> int:
    """Best-effort on-hand inventory lookup. Defaults to 100 when unknown."""
    if sku and sku != "ALL":
        pl = PredictiveLogistics.query.filter_by(variant_sku=sku).first()
        if pl and pl.days_remaining is not None and pl.forecasted_demand_velocity:
            try:
                return int(pl.days_remaining * pl.forecasted_demand_velocity)
            except Exception:
                pass
        cat = LocalProductCatalog.query.filter_by(shopify_product_id=sku).first()
        if cat and cat.inventory_quantity:
            return int(cat.inventory_quantity)

    total = db.session.query(db.func.coalesce(db.func.sum(LocalProductCatalog.inventory_quantity), 0)).scalar()
    return int(total or 100)


def aggregate_telemetry(merchant_id: str, sku: str = "ALL", window_hours: int = 24) -> SKUTelemetry:
    """Aggregate multi-channel sales data into a telemetry snapshot."""
    since = datetime.utcnow() - timedelta(hours=window_hours)

    orders = ProfitFeedOrder.query.filter(
        ProfitFeedOrder.merchant_id == merchant_id,
        ProfitFeedOrder.recorded_at >= since,
    ).all()

    revenue = sum(_to_float(o.gross_revenue) for o in orders)
    cogs = sum(_to_float(o.cost_of_goods_sold) for o in orders)
    fees = sum(_to_float(o.marketplace_fees) for o in orders)
    shipping = sum(_to_float(o.shipping_costs) for o in orders)
    refunds = sum(_to_float(o.refund_amount) for o in orders)
    ad_spend = sum(_to_float(o.ad_spend_attributed) for o in orders)
    refund_count = sum(1 for o in orders if _to_float(o.refund_amount) > 0)
    total_orders = len(orders)

    # AdSpendFeed may contain additional unattributed ad spend.
    ad_rows = AdSpendFeed.query.filter(
        AdSpendFeed.merchant_id == merchant_id,
        AdSpendFeed.recorded_at >= since,
    ).all()
    ad_spend += sum(_to_float(a.amount) for a in ad_rows)

    days = max(window_hours / 24.0, 1.0)
    velocity = total_orders / days

    return SKUTelemetry(
        sku=sku,
        revenue_24h=round(revenue, 2),
        cogs_24h=round(cogs, 2),
        ad_spend_24h=round(ad_spend, 2),
        shipping_cost_24h=round(shipping, 2),
        fees_24h=round(fees, 2),
        refunds_24h=round(refunds, 2),
        taxes_24h=0.0,
        on_hand_inventory=_on_hand_inventory(merchant_id, sku),
        daily_sales_velocity=round(velocity, 2),
        refund_count_24h=refund_count,
        total_orders_24h=total_orders,
    )


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
            alert_type=alert_type,
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
    """Evaluate merchant telemetry and write any new alerts/actions."""
    telemetry = aggregate_telemetry(merchant_id, window_hours=window_hours)
    memory = _memory_profile(merchant_id)
    engine = VantaRulesEngine(memory)
    events = engine.evaluate_sku_state(telemetry)
    return save_events(merchant_id, events)


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
