"""Multi-Agent Cooperative Executive Network (COO mesh).

Specialized Finance and Logistics agents evaluate live SKU telemetry, then a
controller synthesizes recommendations and validates them against merchant
business memory before staging actions through the Action Gate.
"""
import datetime
import uuid
from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from models import db, Product, UnifiedOrder, OrderItem, DailyCost, BusinessMemory
import action_gate
import competitor_intelligence
import profit_regression

# Re-export the deterministic rules-engine components so the COO mesh can be
# addressed as a single import surface for benchmarks and integrations.
from rules_engine import VantaRulesEngine, SKUTelemetry, BusinessMemoryProfile


class BusinessConstraints(BaseModel):
    merchant_id: str
    max_cac_threshold: float = 18.00
    floor_margin_percentage: int = 25
    forbidden_discount_skus: List[str] = Field(default_factory=list)


class ChannelTelemetrySnapshot(BaseModel):
    sku: str
    channel: str
    units_sold_24h: int
    revenue_24h: float
    cogs_unit: float
    ad_spend_attributed: float
    shipping_cost_actual: float
    marketplace_fees: float
    refunds_filed_count: int
    on_hand_inventory: int
    competitor_median_price: float
    carrier: Optional[str] = None
    origin_region: Optional[str] = None
    destination_region: Optional[str] = None


class EvaluatedActionDraft(BaseModel):
    action_id: str
    kind: str
    title: str
    payload: Dict
    evidence: Dict = Field(default_factory=dict)
    state: str = "draft"
    created_at: str = Field(default_factory=lambda: datetime.datetime.utcnow().isoformat())


class FinanceAgent:
    """Agent responsible for calculating exact true profit parameters and margin erosion paths."""

    def inspect_margins(self, data: ChannelTelemetrySnapshot) -> Dict:
        total_costs = (
            (data.units_sold_24h * data.cogs_unit)
            + data.ad_spend_attributed
            + data.shipping_cost_actual
            + data.marketplace_fees
        )
        net_profit = data.revenue_24h - total_costs
        margin_pct = (net_profit / data.revenue_24h * 100) if data.revenue_24h > 0 else 0.0
        current_cac = (data.ad_spend_attributed / data.units_sold_24h) if data.units_sold_24h > 0 else 0.0

        return {
            "net_profit": round(net_profit, 2),
            "margin_percentage": round(margin_pct, 2),
            "current_cac": round(current_cac, 2),
            "margin_is_healthy": margin_pct >= 20.0,
        }


class CarrierRouteDelayMetrics(BaseModel):
    """Historical carrier delivery performance for a single route."""

    carrier_name: str
    origin_region: str
    destination_region: str
    historical_average_delay_days: float
    recent_transit_failure_rate: float  # 0.0 - 1.0


class LogisticsAgent:
    """Agent tracking supply velocity, stock exhaustion horizons, and vendor anomalies."""

    def __init__(self, carrier_database: Optional[List[CarrierRouteDelayMetrics]] = None):
        self.carrier_database = carrier_database or []

    def find_route_risk(self, carrier: str, origin: str, dest: str) -> float:
        """Return the historical average delay for a carrier/origin/destination route."""
        for route in self.carrier_database:
            if route.carrier_name == carrier and route.origin_region == origin:
                return route.historical_average_delay_days
        return 0.0

    def recalculate_procurement_runway(
        self,
        base_lead_days: int,
        carrier: str,
        origin: str,
        dest: str,
        sales_velocity: float,
    ) -> Dict:
        """Dynamically adjust reorder points based on carrier delay risk."""
        historical_delay = self.find_route_risk(carrier, origin, dest)

        safety_buffer_days = 2
        if any(
            r.recent_transit_failure_rate >= 0.15
            for r in self.carrier_database
            if r.carrier_name == carrier
        ):
            safety_buffer_days += 4

        adjusted_lead_time_days = base_lead_days + historical_delay + safety_buffer_days
        revised_reorder_trigger_units = int(sales_velocity * adjusted_lead_time_days)

        return {
            "calculated_delay_lag": historical_delay,
            "allocated_safety_buffer": safety_buffer_days,
            "total_adjusted_lead_days": adjusted_lead_time_days,
            "dynamic_reorder_point_units": revised_reorder_trigger_units,
        }

    def inspect_runway(self, data: ChannelTelemetrySnapshot, lead_days: int = 12) -> Dict:
        velocity = max(0.5, data.units_sold_24h)
        days_runway = data.on_hand_inventory / velocity

        result = {
            "days_runway": round(days_runway, 2),
            "is_critical_stockout": days_runway <= 5,
            "requires_replenishment": days_runway <= lead_days,
            "recommended_order_qty": int(velocity * 30),
        }

        # If carrier routing data is supplied, layer in delay-aware reorder math.
        if data.carrier and data.origin_region:
            runway = self.recalculate_procurement_runway(
                base_lead_days=lead_days,
                carrier=data.carrier,
                origin=data.origin_region,
                dest=data.destination_region or "",
                sales_velocity=velocity,
            )
            result.update(runway)
            result["dynamic_reorder_point"] = runway["dynamic_reorder_point_units"]

        return result


class AICOOController:
    """Synthesizes telemetry from specialist agents and drafts validated actions."""

    def __init__(
        self,
        constraints: BusinessConstraints,
        carrier_database: Optional[List[CarrierRouteDelayMetrics]] = None,
    ):
        self.constraints = constraints
        self.finance_dept = FinanceAgent()
        self.logistics_dept = LogisticsAgent(carrier_database=carrier_database)

    def run_autonomous_diagnostic(self, matrix: List[ChannelTelemetrySnapshot]) -> List[EvaluatedActionDraft]:
        staged_actions: List[EvaluatedActionDraft] = []

        for telemetry in matrix:
            fin = self.finance_dept.inspect_margins(telemetry)
            log = self.logistics_dept.inspect_runway(telemetry)

            if fin["margin_percentage"] < self.constraints.floor_margin_percentage:
                action_id = f"ACT_COO_{uuid.uuid4().hex[:6].upper()}"
                staged_actions.append(
                    EvaluatedActionDraft(
                        action_id=action_id,
                        kind="ad_budget",
                        title=f"Scale down underperforming ad spend: {telemetry.sku}",
                        payload={
                            "sku": telemetry.sku,
                            "channel": telemetry.channel,
                            "modification": "REDUCE_BUDGET",
                            "value_percentage": 25.0,
                        },
                        evidence={
                            "confidence_score": 89,
                            "reason": (
                                f"Net profit margin dropped to {fin['margin_percentage']}% "
                                f"(floor limit is {self.constraints.floor_margin_percentage}%)."
                            ),
                            "metrics": [
                                f"Active CAC reached ${fin['current_cac']} vs merchant ceiling ${self.constraints.max_cac_threshold}.",
                                "Attributed 24h ad overhead is pulling product margins below acceptable floor.",
                            ],
                        },
                    )
                )

            if log["is_critical_stockout"]:
                action_id = f"ACT_COO_{uuid.uuid4().hex[:6].upper()}"
                staged_actions.append(
                    EvaluatedActionDraft(
                        action_id=action_id,
                        kind="po",
                        title=f"Urgent procurement restock dispatch: {telemetry.sku}",
                        payload={
                            "sku": telemetry.sku,
                            "channel": telemetry.channel,
                            "suggested_units": log.get("dynamic_reorder_point_units") or log["recommended_order_qty"],
                            "estimated_capital_overhead": round(
                                (log.get("dynamic_reorder_point_units") or log["recommended_order_qty"]) * telemetry.cogs_unit, 2
                            ),
                            "carrier_delay_lag_days": log.get("calculated_delay_lag", 0.0),
                            "safety_buffer_days": log.get("allocated_safety_buffer", 2),
                        },
                        evidence={
                            "confidence_score": 94,
                            "reason": "Fulfillment runout calculation confirms critical stockout threshold reached.",
                            "metrics": [
                                f"Current on-hand status is down to {telemetry.on_hand_inventory} items.",
                                f"Sales trajectory dictates absolute catalog depletion in {log['days_runway']} days.",
                            ],
                        },
                    )
                )

        return staged_actions


def _dominant_channel(merchant_id: str, sku: str, since: datetime.datetime) -> str:
    row = (
        db.session.query(UnifiedOrder.channel)
        .join(OrderItem, OrderItem.order_id == UnifiedOrder.id)
        .filter(
            UnifiedOrder.merchant_id == merchant_id,
            OrderItem.sku == sku,
            UnifiedOrder.created_at >= since,
        )
        .group_by(UnifiedOrder.channel)
        .order_by(db.func.count(UnifiedOrder.id).desc())
        .first()
    )
    return row[0] if row else "shopify"


def _build_snapshots(merchant_id: str, days: int = 1) -> List[ChannelTelemetrySnapshot]:
    since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    products = Product.query.filter_by(merchant_id=merchant_id).all()
    snapshots: List[ChannelTelemetrySnapshot] = []

    for product in products:
        sku = product.sku
        cogs_unit = float(product.unit_cost or 0)
        on_hand = int(product.on_hand or 0)

        line_rows = (
            db.session.query(
                db.func.coalesce(db.func.sum(OrderItem.qty), 0).label("units"),
                db.func.coalesce(db.func.sum(OrderItem.qty * OrderItem.unit_price), 0).label("revenue"),
                db.func.coalesce(db.func.sum(OrderItem.qty * OrderItem.unit_cost), 0).label("cogs"),
                db.func.coalesce(db.func.count(db.distinct(UnifiedOrder.id)), 0).label("orders"),
            )
            .join(UnifiedOrder, UnifiedOrder.id == OrderItem.order_id)
            .filter(
                UnifiedOrder.merchant_id == merchant_id,
                OrderItem.sku == sku,
                UnifiedOrder.created_at >= since,
            )
            .first()
        )

        refund_rows = (
            db.session.query(db.func.count(db.distinct(UnifiedOrder.id)).label("refunds"))
            .join(OrderItem, OrderItem.order_id == UnifiedOrder.id)
            .filter(
                UnifiedOrder.merchant_id == merchant_id,
                OrderItem.sku == sku,
                UnifiedOrder.created_at >= since,
                UnifiedOrder.status.in_(["refunded", "cancelled"]),
            )
            .first()
        )

        # Allocate order-level shipping/fees proportional to this SKU's COGS share.
        order_totals = (
            db.session.query(
                db.func.coalesce(db.func.sum(UnifiedOrder.revenue), 0).label("total_revenue"),
                db.func.coalesce(db.func.sum(UnifiedOrder.shipping_charged), 0).label("total_shipping"),
                db.func.coalesce(db.func.sum(UnifiedOrder.tax), 0).label("total_tax"),
            )
            .join(OrderItem, OrderItem.order_id == UnifiedOrder.id)
            .filter(
                UnifiedOrder.merchant_id == merchant_id,
                OrderItem.sku == sku,
                UnifiedOrder.created_at >= since,
            )
            .first()
        )

        sku_revenue = float(line_rows.revenue or 0)
        total_revenue = float(order_totals.total_revenue or 0) or 1.0
        share = sku_revenue / total_revenue

        shipping = float(order_totals.total_shipping or 0) * share
        # Estimate marketplace fees as 5% of SKU revenue unless we have line-level data.
        marketplace_fees = sku_revenue * 0.05

        log_date = since.date()
        daily = (
            DailyCost.query.filter_by(sku=sku)
            .filter(DailyCost.log_date >= log_date)
            .first()
        )
        ad_spend = float(daily.ad_spend or 0) if daily else 0.0

        channel = _dominant_channel(merchant_id, sku, since)
        market = competitor_intelligence.get_market_evidence(sku, channel)

        snapshots.append(
            ChannelTelemetrySnapshot(
                sku=sku,
                channel=channel,
                units_sold_24h=int(line_rows.units or 0),
                revenue_24h=sku_revenue,
                cogs_unit=cogs_unit,
                ad_spend_attributed=ad_spend,
                shipping_cost_actual=shipping,
                marketplace_fees=marketplace_fees,
                refunds_filed_count=int(refund_rows.refunds or 0),
                on_hand_inventory=on_hand,
                competitor_median_price=float(market.get("competitor_median_price", 0.0) or 0),
            )
        )

    return snapshots


def run_diagnostic(merchant_id: str, days: int = 1, create_actions: bool = True) -> List[Dict]:
    """Run the COO mesh for a merchant and optionally stage actions through the Action Gate."""
    memory = BusinessMemory.query.filter_by(merchant_id=merchant_id).first()
    constraints = BusinessConstraints(
        merchant_id=merchant_id,
        max_cac_threshold=float(memory.max_cac_threshold or 18.0) if memory else 18.0,
        floor_margin_percentage=int(memory.floor_margin_percentage or 20) if memory else 20,
        forbidden_discount_skus=list(memory.forbidden_discount_skus or []) if memory else [],
    )

    snapshots = _build_snapshots(merchant_id, days=days)

    staged: List[Dict] = []

    if snapshots:
        coo = AICOOController(constraints)
        drafts = coo.run_autonomous_diagnostic(snapshots)

        for draft in drafts:
            evidence = draft.evidence or {}
            detail = evidence.get("reason", "Vantav identified an operational issue.")

            if create_actions:
                try:
                    action = action_gate.create_action(
                        merchant_id=merchant_id,
                        action_type=draft.kind,
                        title=draft.title,
                        detail=detail,
                        payload=draft.payload,
                        snapshot={"kpis": {"net_margin": 0.0, "gross_revenue": 0.0}},
                    )
                    staged.append(action_gate.action_to_dict(action))
                except ValueError as e:
                    # Guardrail blocked the draft.
                    staged.append({"blocked": True, "draft": draft.model_dump(), "reason": str(e)})
            else:
                staged.append(draft.model_dump())

    # Layer in mathematical profit regression actions across the merchant catalog.
    try:
        regression_actions = profit_regression.run_regression_for_merchant(
            merchant_id, lookback_days=30, create_actions=create_actions
        )
        staged.extend(regression_actions)
    except Exception as e:
        staged.append({"blocked": True, "draft": "profit_regression", "reason": str(e)})

    return staged


if __name__ == "__main__":
    # Quick smoke test with sample data.
    merchant_rules = BusinessConstraints(
        merchant_id="merchant_shawn_101",
        max_cac_threshold=15.00,
        floor_margin_percentage=22,
        forbidden_discount_skus=["SKU-PREMIUM-KIT"],
    )

    live_channel_feed = [
        ChannelTelemetrySnapshot(
            sku="SKU-TRACK-JACKET",
            channel="tiktok_shop",
            units_sold_24h=50,
            revenue_24h=2500.00,
            cogs_unit=12.00,
            ad_spend_attributed=950.00,
            shipping_cost_actual=400.00,
            marketplace_fees=150.00,
            refunds_filed_count=1,
            on_hand_inventory=300,
            competitor_median_price=54.00,
        ),
        ChannelTelemetrySnapshot(
            sku="SKU-SMART-PODS",
            channel="shopify",
            units_sold_24h=40,
            revenue_24h=1200.00,
            cogs_unit=8.00,
            ad_spend_attributed=100.00,
            shipping_cost_actual=160.00,
            marketplace_fees=40.00,
            refunds_filed_count=0,
            on_hand_inventory=12,
            competitor_median_price=32.00,
        ),
    ]

    ai_coo = AICOOController(constraints=merchant_rules)
    proposed_actions = ai_coo.run_autonomous_diagnostic(matrix=live_channel_feed)

    print("=" * 64)
    print("VANTA // AI COO REPORT ENGINE")
    print("=" * 64)
    for draft in proposed_actions:
        print(f"STAGED ACTION ID: [{draft.action_id}] | KIND: {draft.kind.upper()}")
        print(f"TITLE: {draft.title}")
        print(f"CONFIDENCE RATING: {draft.evidence.get('confidence_score')}%")
        print(f"EXECUTIVE EXPLANATION: {draft.evidence.get('reason')}")
        print("METRICS EVIDENCE SNAPSHOT:")
        for metric in draft.evidence.get("metrics", []):
            print(f"  - {metric}")
        print(f"PAYLOAD: {draft.payload}")
        print("-" * 64)
