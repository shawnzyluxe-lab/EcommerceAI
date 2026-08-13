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


class LogisticsAgent:
    """Agent tracking supply velocity, stock exhaustion horizons, and vendor anomalies."""

    def inspect_runway(self, data: ChannelTelemetrySnapshot, lead_days: int = 12) -> Dict:
        velocity = max(0.5, data.units_sold_24h)
        days_runway = data.on_hand_inventory / velocity

        return {
            "days_runway": round(days_runway, 2),
            "is_critical_stockout": days_runway <= 5,
            "requires_replenishment": days_runway <= lead_days,
            "recommended_order_qty": int(velocity * 30),
        }


class AICOOController:
    """Synthesizes telemetry from specialist agents and drafts validated actions."""

    def __init__(self, constraints: BusinessConstraints):
        self.constraints = constraints
        self.finance_dept = FinanceAgent()
        self.logistics_dept = LogisticsAgent()

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
                            "suggested_units": log["recommended_order_qty"],
                            "estimated_capital_overhead": round(log["recommended_order_qty"] * telemetry.cogs_unit, 2),
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
    if not snapshots:
        return []

    coo = AICOOController(constraints)
    drafts = coo.run_autonomous_diagnostic(snapshots)

    staged: List[Dict] = []
    for draft in drafts:
        evidence = draft.evidence or {}
        detail = evidence.get("reason", "AI COO identified an operational issue.")

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
