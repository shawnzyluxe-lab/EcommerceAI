"""TikTok Marketing Studio integration for Vantav.

Tracks TikTok ad campaigns and shop affiliates, evaluates true ROAS including
platform coupons and affiliate commissions, and drafts ad-adjust actions
when marketing overhead compresses margins.
"""

import json
import logging
import os
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel

from models import db, MarketingCampaign, ShopAffiliate
import channels as channels_module

logger = logging.getLogger(__name__)


class AdSetPerformanceMetric(BaseModel):
    campaign_id: str
    campaign_name: str
    sku_target: str
    spend_24h: float
    attributed_gmv_24h: float
    platform_coupons_cost: float
    affiliate_commissions_cost: float


class TikTokMarketingStudio:
    """Evaluate TikTok ad/overhead performance and queue corrective actions."""

    TIKTOK_BUSINESS_BASE = "https://business-api.tiktok.com/open_api/v1.3"
    SYMPHONY_BASE = os.environ.get("TIKTOK_SYMPHONY_BASE", "https://open-api.tiktok.com/symphony")

    def __init__(self, merchant_id: str):
        self.merchant_id = merchant_id
        self.creds = self._load_credentials()
        self.access_token = self.creds.get("access_token")
        self.app_key = self.creds.get("app_key")
        self.app_secret = self.creds.get("app_secret")
        self.shop_id = self.creds.get("shop_id")
        self.advertiser_id = self.creds.get("advertiser_id")
        self.developer_token = self.creds.get("developer_token") or os.environ.get("TIKTOK_DEVELOPER_TOKEN")

    def _load_credentials(self) -> Dict[str, Any]:
        try:
            token = channels_module.get_token(self.merchant_id, "tiktok") or "{}"
            return json.loads(token)
        except Exception:
            return {}

    def _business_api_request(self, path: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        if not self.access_token:
            return {"status": "skipped", "reason": "TikTok not connected"}
        url = f"{self.TIKTOK_BUSINESS_BASE}{path}"
        headers = {"Access-Token": self.access_token, "Content-Type": "application/json"}
        try:
            resp = requests.get(url, headers=headers, params=params or {}, timeout=20)
            if resp.status_code == 200:
                return resp.json() or {}
            logger.warning("[TikTok Marketing] %s: %s", resp.status_code, resp.text[:200])
            return {"status": "failed", "reason": resp.text[:200]}
        except Exception:
            logger.exception("[TikTok Marketing] business API request failed")
            return {"status": "failed", "reason": "request failed"}

    def sync_campaigns(self) -> Dict[str, Any]:
        """Pull active TikTok ad campaigns and store/refresh local rows."""
        if not self.advertiser_id:
            return {"status": "skipped", "reason": "TikTok Ads advertiser_id not configured"}

        data = self._business_api_request(
            "/campaign/get/",
            {"advertiser_id": self.advertiser_id, "filtering": json.dumps({"status": "CAMPAIGN_STATUS_ACTIVE"})},
        )
        if data.get("status") in ("skipped", "failed"):
            return data

        items = data.get("list", []) if isinstance(data, dict) else []
        updated = 0
        for item in items:
            campaign_id = str(item.get("campaign_id"))
            if not campaign_id:
                continue
            campaign = MarketingCampaign.query.filter_by(
                merchant_id=self.merchant_id, external_campaign_id=campaign_id
            ).first()
            if not campaign:
                campaign = MarketingCampaign(
                    merchant_id=self.merchant_id,
                    external_campaign_id=campaign_id,
                    campaign_name=item.get("name") or item.get("campaign_name") or "TikTok Campaign",
                    channel="tiktok_ads",
                )
                db.session.add(campaign)
            campaign.daily_budget = Decimal(item.get("budget", 0.0) or 0.0)
            campaign.status = "active"
            campaign.updated_at = db.func.now()
            updated += 1
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("[TikTok Marketing] failed to persist campaigns")
            return {"status": "failed", "reason": "database error"}
        return {"status": "ok", "updated": updated}

    @staticmethod
    def evaluate_marketing_overhead_caps(metric: AdSetPerformanceMetric, margin_floor: float = 25.0) -> Dict[str, Any]:
        """Return true ROAS and overhead diagnostics for a campaign metric."""
        total_marketing_cost = metric.spend_24h + metric.platform_coupons_cost + metric.affiliate_commissions_cost
        real_roas = metric.attributed_gmv_24h / max(1.0, total_marketing_cost)
        overhead_ratio = total_marketing_cost / max(1.0, metric.attributed_gmv_24h)

        action_required = False
        mitigation_strategy = "OPTIMAL_PERFORMANCE"

        # If overhead consumes more than the allowed non-margin share of GMV, flag it.
        if real_roas < 2.0 or total_marketing_cost > (metric.attributed_gmv_24h * 0.40):
            action_required = True
            mitigation_strategy = "PAUSE_CAMPAIGN_OVERHEAD" if real_roas < 1.5 else "REDUCE_BUDGET"

        return {
            "campaign_id": metric.campaign_id,
            "campaign_name": metric.campaign_name,
            "sku_target": metric.sku_target,
            "calculated_total_marketing_overhead": round(total_marketing_cost, 2),
            "blended_marketing_roas": round(real_roas, 2),
            "marketing_overhead_ratio": round(overhead_ratio * 100, 2),
            "margin_floor": float(margin_floor),
            "requires_immediate_mitigation": action_required,
            "recommended_action": mitigation_strategy,
        }

    def evaluate_all_active_campaigns(self) -> List[Dict[str, Any]]:
        """Evaluate every active MarketingCampaign and create Vantav actions for underperformers."""
        import action_gate

        results: List[Dict[str, Any]] = []
        campaigns = MarketingCampaign.query.filter_by(
            merchant_id=self.merchant_id, status="active"
        ).all()

        for campaign in campaigns:
            metric = AdSetPerformanceMetric(
                campaign_id=campaign.external_campaign_id,
                campaign_name=campaign.campaign_name,
                sku_target=campaign.sku_target or "",
                spend_24h=float(campaign.current_spend_24h or 0.0),
                attributed_gmv_24h=float(campaign.attributed_revenue_24h or 0.0),
                platform_coupons_cost=float(campaign.platform_coupons_cost or 0.0),
                affiliate_commissions_cost=float(campaign.affiliate_commissions_cost or 0.0),
            )
            evaluation = self.evaluate_marketing_overhead_caps(metric)
            campaign.active_roas = Decimal(str(evaluation["blended_marketing_roas"]))
            db.session.add(campaign)

            if evaluation["requires_immediate_mitigation"]:
                action = action_gate.create_action(
                    merchant_id=self.merchant_id,
                    action_type="ad_adjust",
                    title=f"Reduce ad overhead for {campaign.campaign_name}",
                    detail=(
                        f"True ROAS is {evaluation['blended_marketing_roas']} and total marketing overhead "
                        f"(${evaluation['calculated_total_marketing_overhead']}) exceeds the safe limit for "
                        f"campaign {campaign.external_campaign_id}. Vantav recommends reducing budget."
                    ),
                    payload={
                        "platform": "tiktok_ads",
                        "campaign_id": campaign.external_campaign_id,
                        "sku_target": campaign.sku_target,
                        "adjustment": -20.0,
                        "blended_roas": evaluation["blended_marketing_roas"],
                        "total_overhead": evaluation["calculated_total_marketing_overhead"],
                    },
                    snapshot={"kpis": {"gross_revenue": float(campaign.attributed_revenue_24h or 0.0)}},
                )
                evaluation["action_id"] = action.id

            results.append(evaluation)

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("[TikTok Marketing] failed to persist evaluations")
        return results

    def trigger_creative_refresh(self, sku: str, branding_brief: str) -> Dict[str, Any]:
        """Queue or dispatch a creative refresh brief for a TikTok ad SKU."""
        endpoint = os.environ.get("TIKTOK_SYMPHONY_API_URL")
        task_id = f"vantav_creative_{sku}_{int(time.time())}"
        if not endpoint:
            logger.info("[TikTok Marketing] Creative refresh staged for SKU %s", sku)
            return {
                "status": "creative_generation_staged",
                "sku": sku,
                "task_id": task_id,
                "expected_render_duration_seconds": 120,
            }

        payload = {
            "sku_reference": sku,
            "creative_agent_mode": "UGC_PROD_DEMO",
            "voiceover_language": "en-US",
            "prompt_brief": f"High energy Gen-Z hook. {branding_brief}",
        }
        try:
            resp = requests.post(
                endpoint,
                headers={"Authorization": f"Bearer {self.developer_token or ''}", "Content-Type": "application/json"},
                json=payload,
                timeout=30,
            )
            if resp.status_code in (200, 201, 202):
                data = resp.json() if resp.text else {}
                return {
                    "status": "creative_generation_dispatched",
                    "sku": sku,
                    "task_id": data.get("task_id", data.get("id", task_id)),
                    "expected_render_duration_seconds": 120,
                }
            logger.warning("[TikTok Marketing] creative refresh failed %s: %s", resp.status_code, resp.text[:200])
            return {"status": "failed", "reason": resp.text[:200]}
        except Exception as e:
            logger.exception("[TikTok Marketing] creative refresh request failed")
            return {"status": "failed", "reason": str(e)}


def evaluate_merchant_marketing(merchant_id: str) -> Dict[str, Any]:
    """Convenience entrypoint: sync and evaluate TikTok campaigns for a merchant."""
    studio = TikTokMarketingStudio(merchant_id)
    sync = studio.sync_campaigns()
    evaluations = studio.evaluate_all_active_campaigns()
    return {"sync": sync, "evaluations": evaluations}
