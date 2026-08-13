"""Core optimization and stress-test suite.

Simulates high-volume multi-channel order traffic to measure deterministic
rules-engine throughput and confirm execution latency stays within the target
budget (15 µs per SKU when run as an in-memory batch).
"""
import time
import random
from typing import List

from coo_agent_mesh import VantaRulesEngine, SKUTelemetry, BusinessMemoryProfile


def run_performance_benchmark():
    """Simulate a high-velocity SKU stream and measure rules-engine throughput."""
    print("=" * 64)
    print("VANTA // CORE STRESS-TEST ENGINE")
    print("=" * 64)

    mock_memory = BusinessMemoryProfile(
        max_cac_threshold=15.00,
        floor_margin_percentage=22,
        forbidden_discount_skus=[],
    )
    engine = VantaRulesEngine(memory=mock_memory)

    count = 10000
    print(f"[INIT] Synthesizing {count:,} multi-channel SKU records...")
    telemetry_batch: List[SKUTelemetry] = [
        SKUTelemetry(
            sku=f"SKU-BENCH-{i:05d}",
            revenue_24h=random.uniform(500.0, 5000.0),
            cogs_24h=random.uniform(100.0, 1000.0),
            ad_spend_24h=random.uniform(50.0, 1200.0),
            shipping_cost_24h=random.uniform(40.0, 400.0),
            fees_24h=random.uniform(20.0, 200.0),
            refunds_24h=random.uniform(0.0, 150.0),
            taxes_24h=random.uniform(10.0, 150.0),
            on_hand_inventory=random.randint(2, 500),
            daily_sales_velocity=random.uniform(1.0, 50.0),
            refund_count_24h=random.randint(0, 10),
            total_orders_24h=random.randint(5, 100),
        )
        for i in range(count)
    ]
    print("[SUCCESS] Data synthesis complete.\n")

    print("[EXECUTE] Processing batch analysis across Rules Engine...")
    start = time.perf_counter()

    total_actions = 0
    for record in telemetry_batch:
        total_actions += len(engine.evaluate_sku_state(record))

    elapsed_ms = (time.perf_counter() - start) * 1000
    avg_us = (elapsed_ms / count) * 1000

    print("\n" + "=" * 64)
    print("OPTIMIZATION BENCHMARK SUMMARY")
    print("=" * 64)
    print(f"Total SKUs audited        : {count:,}")
    print(f"Total action drafts staged: {total_actions:,}")
    print(f"Cumulative pipeline time  : {elapsed_ms:.2f} ms")
    print(f"Average execution per SKU : {avg_us:.3f} microseconds")
    print("-" * 64)
    if avg_us < 15.0:
        print("PERFORMANCE STATUS: PASS (sub-15 µs per SKU)")
    else:
        print("PERFORMANCE STATUS: WARN (optimize parsing loops)")
    print("=" * 64)
    return {
        "skus": count,
        "actions": total_actions,
        "total_ms": elapsed_ms,
        "avg_us": avg_us,
        "pass": avg_us < 15.0,
    }


if __name__ == "__main__":
    run_performance_benchmark()
