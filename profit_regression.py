"""OLS linear regression engine over true profit waterfall data.

This module computes a weighted ordinary least-squares fit across per-SKU daily
profit points and translates a structurally degraded trend into a corrective
draft action for the Action Gate.
"""

import math
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Dict, Tuple, Optional

from pydantic import BaseModel

from models import db, OrderItem, UnifiedOrder, DailyCost, Product, BusinessMemory


# ==========================================
# Regression Mathematical Data Schemas
# ==========================================

class ProfitWaterfallPoint(BaseModel):
    """A single daily profit observation for a SKU."""

    days_ago: int
    gross_revenue: float
    net_profit: float


class RegressionAnalysisReport(BaseModel):
    """OLS regression result for a single SKU."""

    sku: str
    slope_rate_of_change: float
    intercept_baseline: float
    r_squared_confidence: float
    trend_direction: str
    projected_net_profit_7d: float


# ==========================================
# System Reasoning & Math Computation Core
# ==========================================

class VantaMathematicalRegressionEngine:
    """Executes algorithmic least-squares regression matrices over true accounting metrics."""

    @staticmethod
    def calculate_linear_regression(
        sku: str, dataset: List[ProfitWaterfallPoint]
    ) -> RegressionAnalysisReport:
        n = len(dataset)
        if n < 3:
            return RegressionAnalysisReport(
                sku=sku,
                slope_rate_of_change=0.0,
                intercept_baseline=0.0,
                r_squared_confidence=0.0,
                trend_direction="UNKNOWN",
                projected_net_profit_7d=0.0,
            )

        sum_x = 0.0
        sum_y = 0.0
        sum_xy = 0.0
        sum_x2 = 0.0
        sum_y2 = 0.0

        for point in dataset:
            x = float(-point.days_ago)
            y = point.net_profit
            sum_x += x
            sum_y += y
            sum_xy += x * y
            sum_x2 += x * x
            sum_y2 += y * y

        denominator_m = (n * sum_x2) - (sum_x ** 2)
        if denominator_m == 0:
            m = 0.0
        else:
            m = ((n * sum_xy) - (sum_x * sum_y)) / denominator_m

        b = (sum_y - (m * sum_x)) / n

        mean_y = sum_y / n
        ss_tot = 0.0
        ss_res = 0.0
        for point in dataset:
            x = float(-point.days_ago)
            y = point.net_profit
            predicted_y = (m * x) + b
            ss_tot += (y - mean_y) ** 2
            ss_res += (y - predicted_y) ** 2

        r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        if m < -15.0 and r_squared > 0.65:
            trend = "DEGRADED"
        elif m > 15.0 and r_squared > 0.65:
            trend = "GROWING"
        else:
            trend = "STABLE"

        projected_7d = (m * 7.0) + b

        return RegressionAnalysisReport(
            sku=sku,
            slope_rate_of_change=round(m, 2),
            intercept_baseline=round(b, 2),
            r_squared_confidence=round(r_squared, 4),
            trend_direction=trend,
            projected_net_profit_7d=round(max(0.0, projected_7d), 2),
        )


# ==========================================
# Data Hydration Helpers
# ==========================================

def _to_float(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def build_waterfall_dataset(
    merchant_id: str, sku: str, lookback_days: int = 30
) -> List[ProfitWaterfallPoint]:
    """Build a per-day profit waterfall for a SKU from orders and daily costs."""
    end_date = date.today()
    start_date = end_date - timedelta(days=lookback_days)

    # Aggregate revenue and cogs per order date.
    rows = (
        db.session.query(
            db.func.date(UnifiedOrder.created_at).label("order_date"),
            db.func.coalesce(db.func.sum(OrderItem.qty * OrderItem.unit_price), 0).label("revenue"),
            db.func.coalesce(db.func.sum(OrderItem.qty * OrderItem.unit_cost), 0).label("cogs"),
            db.func.coalesce(db.func.count(db.distinct(UnifiedOrder.id)), 0).label("order_count"),
        )
        .join(OrderItem, OrderItem.order_id == UnifiedOrder.id)
        .filter(
            UnifiedOrder.merchant_id == merchant_id,
            OrderItem.sku == sku,
            db.func.date(UnifiedOrder.created_at) >= start_date,
            db.func.date(UnifiedOrder.created_at) <= end_date,
        )
        .group_by(db.func.date(UnifiedOrder.created_at))
        .all()
    )

    daily_cost_rows = (
        DailyCost.query.filter(
            DailyCost.sku == sku,
            DailyCost.log_date >= start_date,
            DailyCost.log_date <= end_date,
        )
        .all()
    )
    cost_by_date: Dict[date, float] = {}
    for dc in daily_cost_rows:
        day = dc.log_date
        cost_by_date[day] = (
            _to_float(dc.ad_spend)
            + _to_float(dc.ship_cost)
            + _to_float(dc.fee)
            + _to_float(dc.refund)
            + _to_float(dc.tax)
        )

    revenue_by_date: Dict[date, float] = {}
    cogs_by_date: Dict[date, float] = {}
    for row in rows:
        day = row.order_date
        if day is None:
            continue
        revenue_by_date[day] = _to_float(row.revenue)
        cogs_by_date[day] = _to_float(row.cogs)

    points: List[ProfitWaterfallPoint] = []
    for i in range(lookback_days + 1):
        day = end_date - timedelta(days=i)
        revenue = revenue_by_date.get(day, 0.0)
        cogs = cogs_by_date.get(day, 0.0)
        costs = cost_by_date.get(day, 0.0)
        net = revenue - cogs - costs
        points.append(
            ProfitWaterfallPoint(
                days_ago=i, gross_revenue=revenue, net_profit=net
            )
        )

    return [p for p in points if p.gross_revenue > 0 or p.net_profit != 0]


def analyze_sku(
    merchant_id: str, sku: str, lookback_days: int = 30
) -> Optional[RegressionAnalysisReport]:
    """Run the OLS regression engine on a SKU's historical profit data."""
    dataset = build_waterfall_dataset(merchant_id, sku, lookback_days=lookback_days)
    if len(dataset) < 3:
        return None
    return VantaMathematicalRegressionEngine.calculate_linear_regression(sku, dataset)


def check_regression_and_stage_action(
    merchant_id: str,
    sku: str,
    lookback_days: int = 30,
) -> Optional[Dict]:
    """Evaluate a SKU's regression report and return a corrective action draft."""
    report = analyze_sku(merchant_id, sku, lookback_days=lookback_days)
    if not report:
        return None
    if report.trend_direction == "DEGRADED":
        return {
            "kind": "ad_adjust",
            "title": f"Mitigate Profit Collapse: Scale Back Ads on {report.sku}",
            "payload": {
                "sku": report.sku,
                "action": "DECREASE_CAMPAIGN_BUDGET",
                "reduction_percentage": 25.0,
            },
            "evidence": {
                "confidence_score": int(report.r_squared_confidence * 100),
                "reason": f"Mathematical regression confirms net profit is actively collapsing at a rate of ${abs(report.slope_rate_of_change)}/day.",
                "metrics": [
                    f"Statistical modeling verification rating reached {report.r_squared_confidence * 100:.1f}%.",
                    f"Unchecked trajectory maps down to an expected profit ceiling of ${report.projected_net_profit_7d} next week.",
                ],
            },
            "state": "draft",
        }
    return None


def run_regression_for_merchant(
    merchant_id: str,
    lookback_days: int = 30,
    create_actions: bool = True,
) -> List[Dict]:
    """Run regression analysis across a merchant's catalog and optionally stage actions."""
    skus = (
        Product.query.filter_by(merchant_id=merchant_id)
        .with_entities(Product.sku)
        .all()
    )
    results: List[Dict] = []
    for (sku,) in skus:
        action = check_regression_and_stage_action(
            merchant_id, sku, lookback_days=lookback_days
        )
        if not action:
            continue

        if create_actions:
            # Lazy import to avoid circular imports at module load time.
            import action_gate
            try:
                created = action_gate.create_action(
                    merchant_id=merchant_id,
                    action_type=action["kind"],
                    title=action["title"],
                    detail=action["evidence"]["reason"],
                    payload=action["payload"],
                    snapshot={"kpis": {"r_squared": action["evidence"]["confidence_score"]}},
                )
                results.append(action_gate.action_to_dict(created))
            except ValueError as e:
                results.append({"blocked": True, "draft": action, "reason": str(e)})
        else:
            results.append(action)
    return results


# ==========================================
# System Execution Test Block
# ==========================================

if __name__ == "__main__":
    troubled_waterfall_history = [
        ProfitWaterfallPoint(days_ago=4, gross_revenue=1500.0, net_profit=450.0),
        ProfitWaterfallPoint(days_ago=3, gross_revenue=1450.0, net_profit=320.0),
        ProfitWaterfallPoint(days_ago=2, gross_revenue=1600.0, net_profit=210.0),
        ProfitWaterfallPoint(days_ago=1, gross_revenue=1300.0, net_profit=110.0),
        ProfitWaterfallPoint(days_ago=0, gross_revenue=1400.0, net_profit=45.0),
    ]

    report = VantaMathematicalRegressionEngine.calculate_linear_regression(
        sku="SKU-404-PODS",
        dataset=troubled_waterfall_history,
    )

    print("================================================================")
    print("AI COO MATHEMATICAL REGRESSION MATRIX DIAGNOSTIC")
    print("================================================================\n")
    print(f"[+] Product Target Identifier   : {report.sku}")
    print(f"[+] Calculated Slope Vector     : {report.slope_rate_of_change} $/day")
    print(f"[+] Statistical Fit Confidence  : {report.r_squared_confidence * 100:.2f}% (R^2)")
    print(f"[ALARM EVALUATION] Trend State : {report.trend_direction}")
    print(f"[+] Projected Profit (7-Day Curve): ${report.projected_net_profit_7d}")
    print("\n================================================================")
