"""SKU-level predictive forecasting and cash-flow engine.

Pulls from the unified products/orders/order_items/suppliers tables and produces
statistical runway projections (stockout date, recommended reorder date, capital reserve).
"""
import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from models import db, Product, Supplier, UnifiedOrder, OrderItem


class HistoricalSalesPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: datetime.date
    units_sold: int
    revenue: float


class ForecastInputContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sku: str
    on_hand: int
    inbound: int
    lead_days: int
    unit_cost: float
    historical_sales: List[HistoricalSalesPoint] = Field(default_factory=list)


class ForecastOutputReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sku: str
    predicted_daily_velocity: float
    estimated_stockout_date: Optional[datetime.date]
    recommended_restock_date: Optional[datetime.date]
    required_cash_reserve: float
    days_buffer_remaining: int
    suggested_reorder_qty: int


def get_historical_sales(merchant_id: str, sku: str, days: int = 14) -> List[HistoricalSalesPoint]:
    """Aggregate units and revenue sold per day for an SKU over the lookback window."""
    since = datetime.date.today() - datetime.timedelta(days=days)

    rows = (
        db.session.query(
            db.func.date(UnifiedOrder.created_at).label("sale_date"),
            db.func.coalesce(db.func.sum(OrderItem.qty), 0).label("units"),
            db.func.coalesce(db.func.sum(OrderItem.qty * OrderItem.unit_price), 0).label("revenue"),
        )
        .join(OrderItem, UnifiedOrder.id == OrderItem.order_id)
        .filter(
            UnifiedOrder.merchant_id == merchant_id,
            OrderItem.sku == sku,
            db.func.date(UnifiedOrder.created_at) >= since,
            UnifiedOrder.status.notin_(["cancelled", "refunded"]),
        )
        .group_by(db.func.date(UnifiedOrder.created_at))
        .order_by(db.func.date(UnifiedOrder.created_at))
        .all()
    )

    return [
        HistoricalSalesPoint(date=r.sale_date, units_sold=int(r.units), revenue=float(r.revenue))
        for r in rows
    ]


class VantaPredictiveForecaster:
    """Statistical SKU demand forecasting for restock timing and cash reserve."""

    @staticmethod
    def generate_runway_projections(context: ForecastInputContext) -> ForecastOutputReport:
        if not context.historical_sales:
            return ForecastOutputReport(
                sku=context.sku,
                predicted_daily_velocity=0.0,
                estimated_stockout_date=None,
                recommended_restock_date=None,
                required_cash_reserve=0.0,
                days_buffer_remaining=999,
                suggested_reorder_qty=0,
            )

        sorted_history = sorted(context.historical_sales, key=lambda x: x.date, reverse=True)
        total_weight = 0.0
        weighted_units = 0.0
        weighted_revenue = 0.0

        for index, point in enumerate(sorted_history[:14]):
            weight = 1.0 / (index + 1)
            weighted_units += point.units_sold * weight
            weighted_revenue += point.revenue * weight
            total_weight += weight

        velocity = max(0.1, weighted_units / total_weight)
        total_available = context.on_hand + context.inbound
        days_runway = int(total_available / velocity)

        today = datetime.date.today()
        stockout_date = today + datetime.timedelta(days=days_runway)

        days_until_reorder = days_runway - context.lead_days
        recommended_order_date = today + datetime.timedelta(days=max(0, days_until_reorder))

        suggested_reorder_qty = int(velocity * 30)
        capital_reserve = suggested_reorder_qty * context.unit_cost

        return ForecastOutputReport(
            sku=context.sku,
            predicted_daily_velocity=round(velocity, 2),
            estimated_stockout_date=stockout_date,
            recommended_restock_date=recommended_order_date,
            required_cash_reserve=round(capital_reserve, 2),
            days_buffer_remaining=days_until_reorder,
            suggested_reorder_qty=suggested_reorder_qty,
        )


def forecast_sku(merchant_id: str, sku: str, days: int = 14) -> ForecastOutputReport:
    """Build a ForecastInputContext from the database and generate a report."""
    product = Product.query.filter_by(merchant_id=merchant_id, sku=sku).first()
    if not product:
        return ForecastOutputReport(
            sku=sku,
            predicted_daily_velocity=0.0,
            estimated_stockout_date=None,
            recommended_restock_date=None,
            required_cash_reserve=0.0,
            days_buffer_remaining=999,
            suggested_reorder_qty=0,
        )

    supplier = None
    if product.supplier_id:
        supplier = Supplier.query.filter_by(id=product.supplier_id).first()

    historical = get_historical_sales(merchant_id, sku, days=days)
    context = ForecastInputContext(
        sku=sku,
        on_hand=product.on_hand or 0,
        inbound=product.inbound or 0,
        lead_days=supplier.lead_days if supplier else 14,
        unit_cost=float(product.unit_cost or 0),
        historical_sales=historical,
    )
    return VantaPredictiveForecaster.generate_runway_projections(context)


def forecast_all_skus(merchant_id: str, days: int = 14) -> List[ForecastOutputReport]:
    """Run forecasting for every product the merchant has."""
    products = Product.query.filter_by(merchant_id=merchant_id).all()
    return [forecast_sku(merchant_id, p.sku, days=days) for p in products]


if __name__ == "__main__":
    base_date = datetime.date.today()
    mock_sales = [
        HistoricalSalesPoint(date=base_date - datetime.timedelta(days=1), units_sold=45, revenue=1350.0),
        HistoricalSalesPoint(date=base_date - datetime.timedelta(days=2), units_sold=38, revenue=1140.0),
        HistoricalSalesPoint(date=base_date - datetime.timedelta(days=3), units_sold=20, revenue=600.0),
        HistoricalSalesPoint(date=base_date - datetime.timedelta(days=4), units_sold=12, revenue=360.0),
        HistoricalSalesPoint(date=base_date - datetime.timedelta(days=5), units_sold=10, revenue=300.0),
    ]

    mock_input = ForecastInputContext(
        sku="SKU-VIRAL-PODS",
        on_hand=120,
        inbound=0,
        lead_days=10,
        unit_cost=7.50,
        historical_sales=mock_sales,
    )

    report = VantaPredictiveForecaster.generate_runway_projections(mock_input)
    print("--- CORE CO-PILOT FORECAST INSIGHTS ---")
    print(f"SKU: {report.sku}")
    print(f"Calculated Velocity: {report.predicted_daily_velocity} units/day")
    print(f"Estimated Stock Exhaustion: {report.estimated_stockout_date}")
    print(f"Target Procurement Date: {report.recommended_restock_date}")
    print(f"Procurement Capital Commitment Required: ${report.required_cash_reserve}")
    print(f"Days Safety Runway Buffer: {report.days_buffer_remaining} days")
    print(f"Suggested Reorder Quantity: {report.suggested_reorder_qty}")
