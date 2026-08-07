import os
import logging
import asyncio
from typing import Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field, HttpUrl
import httpx

from models import db, TrendingProduct, MerchantProfile

logger = logging.getLogger("shawnzyluxe_core")

DEFAULT_TREND_MERCHANT = os.environ.get("DEFAULT_TREND_MERCHANT", "merchant_shawn_01")
TREND_VELOCITY_THRESHOLD = float(os.environ.get("TREND_VELOCITY_THRESHOLD", "25.0"))


class TrendProductMetric(BaseModel):
    source_platform: str
    external_item_id: str
    title: str
    sample_image_url: str
    current_velocity_score: float
    scraped_at: datetime = Field(default_factory=datetime.utcnow)


class TokenizedProxyRotator:
    """Rotating egress gateways to evade target marketplace IP blocklists."""
    def __init__(self):
        self.proxy_pool: List[str] = [
            os.getenv("PROXY_GATE_RESIDENTIAL_A", ""),
            os.getenv("PROXY_GATE_RESIDENTIAL_B", ""),
        ]
        self.proxy_pool = [p for p in self.proxy_pool if p.startswith("http")]
        if not self.proxy_pool:
            self.proxy_pool = ["http://localhost:8080"]
        self._index = 0

    def get_next_gateway(self) -> Dict[str, str]:
        proxy = self.proxy_pool[self._index % len(self.proxy_pool)]
        self._index += 1
        return {"http://": proxy, "https://": proxy}


class TrendingProductsScraper:
    """Automated extraction worker for TikTok and Amazon trending product nodes."""
    def __init__(self):
        self.rotator = TokenizedProxyRotator()
        self.timeout_config = httpx.Timeout(15.0, connect=5.0)

    async def fetch_tiktok_shop_trending_nodes(self) -> List[Dict[str, Any]]:
        """Query TikTok discover endpoints for rising retail tags."""
        proxies = self.rotator.get_next_gateway()
        try:
            async with httpx.AsyncClient(proxies=proxies, timeout=self.timeout_config) as client:
                headers = {"User-Agent": "Mozilla/5.0 Enterprise-Engine Core-v4.02"}
                response = await client.get("https://tiktok.com", headers=headers)
                if response.status_code == 200:
                    try:
                        raw_data = response.json()
                        return raw_data.get("products", [])
                    except ValueError:
                        return []
        except httpx.RequestError as e:
            logger.warning(f"[SCRAPER] TikTok worker connection failed: {e}")
        return []

    async def fetch_amazon_bestseller_velocity(self, category_node: str) -> List[Dict[str, Any]]:
        """Extract current trending items from a category directory."""
        proxies = self.rotator.get_next_gateway()
        try:
            async with httpx.AsyncClient(proxies=proxies, timeout=self.timeout_config) as client:
                url = f"https://amazon.com{category_node}"
                response = await client.get(url, headers={"User-Agent": "Mozilla/5.0 v4.02"})
                if response.status_code == 200:
                    # In production, use an adaptive parser like Scrapling to extract JSON.
                    # For now, return a simulation payload for the worker pipeline.
                    return [
                        {"asin": "B07XJ8C86P", "rank_change": 42.5, "title": "Ergonomic Grip Mat"},
                        {"asin": "B08N5WRWNW", "rank_change": 18.0, "title": "Resistance Band Set"},
                    ]
        except httpx.RequestError as e:
            logger.warning(f"[SCRAPER] Amazon worker connection failed: {e}")
        return []

    async def execute_automation_loop(self):
        """Main lifecycle entry point: scrape, normalize, and write to DB."""
        logger.info("[TREND WORKER] Initiating automated product discovery cycle...")

        # Ensure target merchant exists
        profile = MerchantProfile.query.get(DEFAULT_TREND_MERCHANT)
        if not profile:
            logger.warning("[TREND WORKER] Default merchant not found; skipping flush.")
            return 0

        tiktok_feed, amazon_feed = await asyncio.gather(
            self.fetch_tiktok_shop_trending_nodes(),
            self.fetch_amazon_bestseller_velocity("/sports-and-fitness"),
        )

        normalized_records: List[TrendProductMetric] = []

        for item in tiktok_feed:
            normalized_records.append(TrendProductMetric(
                source_platform="TikTok_Shop",
                external_item_id=str(item.get("product_id", item.get("id", "unknown"))),
                title=str(item.get("title", "Untitled")),
                sample_image_url=str(item.get("image_url", "https://platform.network")),
                current_velocity_score=float(item.get("velocity", 0.0)),
            ))

        for item in amazon_feed:
            normalized_records.append(TrendProductMetric(
                source_platform="Amazon_Bestsellers",
                external_item_id=item["asin"],
                title=item["title"],
                sample_image_url="https://platform.network",
                current_velocity_score=item["rank_change"],
            ))

        # Write to the central multi-tenant DB
        for metric in normalized_records:
            tier = "Tier 1" if metric.current_velocity_score >= TREND_VELOCITY_THRESHOLD else "Tier 2"
            db.session.add(TrendingProduct(
                merchant_id=DEFAULT_TREND_MERCHANT,
                source_platform=metric.source_platform,
                external_item_id=metric.external_item_id,
                title=metric.title,
                sample_image_url=metric.sample_image_url,
                current_velocity_score=metric.current_velocity_score,
                scraped_at=metric.scraped_at,
                tier=tier,
                alert_status="Active",
            ))
        db.session.commit()

        logger.info(f"[TREND WORKER] Flush complete. {len(normalized_records)} products synchronized to DB.")
        return len(normalized_records)


def run_trend_scrape():
    """Synchronous wrapper for the async trend worker."""
    return asyncio.run(TrendingProductsScraper().execute_automation_loop())
