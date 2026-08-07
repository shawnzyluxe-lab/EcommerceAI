from pydantic import BaseModel, Field
from typing import List


class WarehouseInventoryNode(BaseModel):
    warehouse_id: str = Field(..., description="e.g. FBA_EAST_01, 3PL_WEST_MAIN")
    zip_code: str
    available_stock: int
    transit_cost_multiplier: float


class OrderRoutingPayload(BaseModel):
    order_id: str
    target_sku: str
    shipping_zip: str
    quantity: int


class AISmartRouter:
    @staticmethod
    def calculate_optimal_hub(order: OrderRoutingPayload, inventory_pool: List[WarehouseInventoryNode]) -> str:
        """
        Algorithmic Selection: Identifies nodes containing required quantities,
        then scores them based on zip proximity indicators.
        """
        valid_nodes = [node for node in inventory_pool if node.available_stock >= order.quantity]

        if not valid_nodes:
            return "ROUTE_FAILED_INSUFFICIENT_GLOBAL_STOCK"

        best_node = min(
            valid_nodes,
            key=lambda x: abs(int(x.zip_code[:3]) - int(order.shipping_zip[:3])) * x.transit_cost_multiplier
        )
        return best_node.warehouse_id
