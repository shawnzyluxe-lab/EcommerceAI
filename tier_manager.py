"""Central tier policy engine for Vantav."""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from models import db, MerchantProfile, SaaSBilling, MerchantChannel, PendingAction

TIER_LIMITS: Dict[str, Dict[str, Any]] = {
    "Basic Tier": {
        "display_name": "Basic Tier",
        "monthly_order_limit": 500,
        "max_monthly_actions": 50,
        "max_store_connections": 2,
        "sync_frequency_seconds": 3600,
        "support_level": "Standard",
        "max_users": 1,
        "features_allowed": ["overview", "profit", "alerts", "actions", "shopify", "support"],
        "advanced_automation": False,
        "cross_store": False,
        "portfolio": False,
        "custom_alerts": False,
        "api_access": False,
    },
    # Legacy beta tiers mapped to Growth-equivalent limits for existing accounts.
    "Beta Tier": {
        "display_name": "Vantav Growth",
        "monthly_order_limit": 5000,
        "max_monthly_actions": 300,
        "max_store_connections": 5,
        "sync_frequency_seconds": 900,
        "support_level": "Priority",
        "max_users": 10,
        "features_allowed": [
            "overview", "profit", "alerts", "actions", "inventory_forecast",
            "advanced_forecast", "cross_store", "action_audit",
            "shopify", "tiktok", "amazon", "support", "marketing",
        ],
        "advanced_automation": True,
        "cross_store": True,
        "portfolio": False,
        "custom_alerts": False,
        "api_access": False,
    },
    "Beta + Startup Pack": {
        "display_name": "Vantav Growth + Concierge",
        "monthly_order_limit": 5000,
        "max_monthly_actions": 300,
        "max_store_connections": 5,
        "sync_frequency_seconds": 900,
        "support_level": "Priority",
        "max_users": 10,
        "features_allowed": [
            "overview", "profit", "alerts", "actions", "inventory_forecast",
            "advanced_forecast", "cross_store", "action_audit",
            "shopify", "tiktok", "amazon", "support", "marketing",
        ],
        "advanced_automation": True,
        "cross_store": True,
        "portfolio": False,
        "custom_alerts": False,
        "api_access": False,
    },
    "Pro Tier": {
        "display_name": "Vantav Growth",
        "monthly_order_limit": 50000,
        "max_monthly_actions": 300,
        "max_store_connections": 5,
        "sync_frequency_seconds": 900,
        "support_level": "Priority",
        "max_users": 10,
        "features_allowed": [
            "overview", "profit", "alerts", "actions", "inventory_forecast",
            "advanced_forecast", "cross_store", "action_audit",
            "shopify", "tiktok", "amazon", "support", "marketing",
        ],
        "advanced_automation": True,
        "cross_store": True,
        "portfolio": False,
        "custom_alerts": False,
        "api_access": False,
    },
    "Enterprise AI Tier": {
        "display_name": "Vantav Scale",
        "monthly_order_limit": 999999,
        "max_monthly_actions": 999999,
        "max_store_connections": 15,
        "sync_frequency_seconds": 120,
        "support_level": "Priority",
        "max_users": 25,
        "features_allowed": [
            "overview", "profit", "alerts", "actions", "inventory_forecast",
            "advanced_forecast", "cross_store", "action_audit", "portfolio",
            "custom_alerts", "api_access",
            "shopify", "tiktok", "amazon", "support", "marketing",
        ],
        "advanced_automation": True,
        "cross_store": True,
        "portfolio": True,
        "custom_alerts": True,
        "api_access": True,
    },
    "Vantav Operator": {
        "display_name": "Vantav Operator",
        "monthly_order_limit": 1000,
        "max_monthly_actions": 50,
        "max_store_connections": 2,
        "sync_frequency_seconds": 3600,
        "support_level": "Standard",
        "max_users": 3,
        "features_allowed": [
            "overview", "profit", "alerts", "actions", "inventory_forecast",
            "shopify", "tiktok", "amazon", "support",
        ],
        "advanced_automation": False,
        "cross_store": False,
        "portfolio": False,
        "custom_alerts": False,
        "api_access": False,
    },
    "Vantav Growth": {
        "display_name": "Vantav Growth",
        "monthly_order_limit": 5000,
        "max_monthly_actions": 300,
        "max_store_connections": 5,
        "sync_frequency_seconds": 900,
        "support_level": "Priority",
        "max_users": 10,
        "features_allowed": [
            "overview", "profit", "alerts", "actions", "inventory_forecast",
            "advanced_forecast", "cross_store", "action_audit",
            "shopify", "tiktok", "amazon", "support", "marketing",
        ],
        "advanced_automation": True,
        "cross_store": True,
        "portfolio": False,
        "custom_alerts": False,
        "api_access": False,
    },
    "Vantav Scale": {
        "display_name": "Vantav Scale",
        "monthly_order_limit": 50000,
        "max_monthly_actions": 999999,
        "max_store_connections": 15,
        "sync_frequency_seconds": 120,
        "support_level": "Priority",
        "max_users": 25,
        "features_allowed": [
            "overview", "profit", "alerts", "actions", "inventory_forecast",
            "advanced_forecast", "cross_store", "action_audit", "portfolio",
            "custom_alerts", "api_access",
            "shopify", "tiktok", "amazon", "support", "marketing",
        ],
        "advanced_automation": True,
        "cross_store": True,
        "portfolio": True,
        "custom_alerts": True,
        "api_access": True,
    },
    "Concierge Bundle": {
        "display_name": "Concierge Bundle",
        "monthly_order_limit": 50000,
        "max_monthly_actions": 999999,
        "max_store_connections": 15,
        "sync_frequency_seconds": 120,
        "support_level": "Priority",
        "max_users": 25,
        "features_allowed": [
            "overview", "profit", "alerts", "actions", "inventory_forecast",
            "advanced_forecast", "cross_store", "action_audit", "portfolio",
            "custom_alerts", "api_access",
            "shopify", "tiktok", "amazon", "support", "marketing",
        ],
        "advanced_automation": True,
        "cross_store": True,
        "portfolio": True,
        "custom_alerts": True,
        "api_access": True,
    },
}

TIER_ORDER = {
    "Basic Tier": 0,
    "Operator": 1,
    "Vantav Operator": 1,
    "Beta Tier": 2,
    "Pro Tier": 2,
    "Growth": 2,
    "Vantav Growth": 2,
    "Scale": 3,
    "Enterprise AI Tier": 3,
    "Enterprise Plan": 3,
    "Vantav Scale": 3,
    "Concierge Bundle": 3,
}

TIER_PRICE_ENV = {
    "operator": "STRIPE_PRICE_OPERATOR_MONTHLY",
    "growth": "STRIPE_PRICE_GROWTH_MONTHLY",
    "scale": "STRIPE_PRICE_SCALE_MONTHLY",
    "concierge_bundle": "STRIPE_PRICE_CONCIERGE_BUNDLE_MONTHLY",
    "beta": "STRIPE_PRICE_BETA_MONTHLY",
    "beta_startup": "STRIPE_PRICE_BETA_STARTUP",
}

PLAN_TO_TIER = {
    "operator": "Vantav Operator",
    "growth": "Vantav Growth",
    "scale": "Vantav Scale",
    "concierge_bundle": "Concierge Bundle",
    "beta": "Vantav Growth",
    "beta_startup": "Vantav Growth",
}

TIER_PAGE_ACCESS = {
    "overview": "Vantav Operator",
    "alerts": "Vantav Operator",
    "action_gate": "Vantav Operator",
    "profit_engine": "Vantav Operator",
    "regression_chart": "Vantav Operator",
    "billing": "Vantav Operator",
    "commerce_hub": "Vantav Operator",
    "command_center": "Vantav Growth",
    "monitoring": "Vantav Growth",
    "predictions": "Vantav Growth",
    "health_score": "Vantav Growth",
    "marketing": "Vantav Growth",
    "automations": "Vantav Growth",
    "team_ai": "Vantav Growth",
    "product_research": "Vantav Growth",
    "fulfillment": "Vantav Growth",
    "fraud": "Vantav Scale",
    "suppliers": "Vantav Scale",
    "startup_pack": "Vantav Scale",
}


class TierManager:
    """Enforces tenant tier limits and feature flags."""

    @staticmethod
    def get_tier_meta(tier: str) -> Dict[str, Any]:
        return TIER_LIMITS.get(tier, TIER_LIMITS["Basic Tier"])

    @staticmethod
    def tier_rank(tier: str) -> int:
        return TIER_ORDER.get(tier, 0)

    @staticmethod
    def get_store_limit(tier: str) -> int:
        return TierManager.get_tier_meta(tier).get("max_store_connections", 2)

    @staticmethod
    def get_action_limit(tier: str) -> int:
        return TierManager.get_tier_meta(tier).get("max_monthly_actions", 50)

    @staticmethod
    def get_order_limit(tier: str) -> int:
        return TierManager.get_tier_meta(tier).get("monthly_order_limit", 500)

    @staticmethod
    def has_feature(tier: str, feature: str) -> bool:
        return feature in TierManager.get_tier_meta(tier).get("features_allowed", [])

    @staticmethod
    def current_store_count(merchant_id: str) -> int:
        return MerchantChannel.query.filter_by(merchant_id=merchant_id).count()

    @staticmethod
    def current_action_count(merchant_id: str) -> int:
        now = datetime.utcnow()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return PendingAction.query.filter(
            PendingAction.merchant_id == merchant_id,
            PendingAction.status.in_(["approved", "executed"]),
            PendingAction.decided_at >= start,
        ).count()

    @staticmethod
    def can_add_store(merchant_id: str) -> bool:
        profile = MerchantProfile.query.get(merchant_id)
        if not profile:
            return False
        tier = profile.account_tier or "Basic Tier"
        limit = TierManager.get_store_limit(tier)
        return TierManager.current_store_count(merchant_id) < limit

    @staticmethod
    def can_execute_action(merchant_id: str) -> bool:
        profile = MerchantProfile.query.get(merchant_id)
        if not profile:
            return False
        tier = profile.account_tier or "Basic Tier"
        limit = TierManager.get_action_limit(tier)
        return TierManager.current_action_count(merchant_id) < limit

    @staticmethod
    def verify_operational_allowance(merchant_id: str, current_usage: int) -> tuple[bool, str]:
        profile = MerchantProfile.query.get(merchant_id)
        if not profile:
            return False, "Unknown merchant"
        account = SaaSBilling.query.get(merchant_id)
        if not account:
            return False, "No billing record"
        tier = profile.account_tier or "Basic Tier"
        monthly_order_limit = TierManager.get_order_limit(tier)
        if current_usage >= monthly_order_limit:
            return False, f"LIMIT EXCEEDED: Brand has consumed its allotment of {monthly_order_limit} orders for this billing cycle. Please upgrade."
        return True, "OK"

    @staticmethod
    def route_order_automation(merchant_id: str, order_data: dict) -> dict:
        profile = MerchantProfile.query.get(merchant_id)
        if not profile:
            return {"status": "SKIPPED", "reason": "Unknown merchant"}
        tier = profile.account_tier or "Basic Tier"
        meta = TierManager.get_tier_meta(tier)
        if not meta.get("advanced_automation"):
            return {"status": "SKIPPED", "reason": "Upgrade required for autonomous routing."}
        return {"status": "DISPATCHED", "destination": "optimal_hub", "order_id": order_data.get("order_id")}

    @staticmethod
    def can_access_page(tier: str, page: str) -> bool:
        required = TIER_PAGE_ACCESS.get(page)
        if not required:
            return True
        return TierManager.tier_rank(tier) >= TierManager.tier_rank(required)

    @staticmethod
    def page_upgrade_target(page: str) -> str:
        return TIER_PAGE_ACCESS.get(page, "Vantav Growth")
