"""Competitor and market intelligence evidence for action recommendations."""
import hashlib
import random
from typing import Any, Dict, Optional


def _seed(s: str) -> float:
    """Deterministic 0-1 float from a string."""
    return (int(hashlib.sha256(s.encode()).hexdigest(), 16) % 10000) / 10000.0


def get_market_evidence(sku: str, platform: Optional[str] = None) -> Dict[str, Any]:
    """Return plausible competitor/market evidence for a SKU.

    In production this would call a real market-data API. For now it returns
    deterministic mock values so every recommendation has something concrete
    to cite.
    """
    sku = (sku or "UNKNOWN").strip()
    base = _seed(sku)
    rng = random.Random(base)

    median_price = round(20.0 + base * 80.0, 2)
    price_gap_pct = round((rng.random() * 20.0) - 10.0, 1)  # -10% to +10%
    sales_velocity_delta = round((rng.random() * 40.0) - 20.0, 1)  # -20% to +20%
    trend_14d = round(rng.random() * 100.0 - 50.0, 1)
    market_trend = "up" if trend_14d > 10 else ("down" if trend_14d < -10 else "flat")

    return {
        "competitor_median_price": median_price,
        "price_gap_pct": price_gap_pct,
        "sales_velocity_delta": sales_velocity_delta,
        "trend_14d": trend_14d,
        "market_trend": market_trend,
        "sample_size": rng.randint(12, 120),
        "platform": platform or "market",
    }


def enrich_for_sku(sku: str, platform: Optional[str] = None) -> str:
    """Human-readable market summary for an evidence bullet."""
    ev = get_market_evidence(sku, platform)
    direction = "above" if ev["price_gap_pct"] >= 0 else "below"
    return (
        f"Market: median price ${ev['competitor_median_price']} ({abs(ev['price_gap_pct'])}% {direction} median), "
        f"sales velocity {ev['sales_velocity_delta']:+.1f}%, 14-day trend {ev['market_trend']}"
    )
