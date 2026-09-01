# Copyright (c) 2026 Vantav / Shawnzyluxe. All rights reserved.
# This file is part of the Vantav Commerce Platform and is proprietary software.
# Unauthorized copying, modification, distribution, or use is strictly prohibited.
# See LICENSE for the full proprietary license terms.

"""Central tier policy engine for Vantav."""
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from models import db, MerchantProfile, SaaSBilling, MerchantChannel, PendingAction, UnifiedOrder, Product, WorkspaceSeat

TIER_LIMITS: Dict[str, Dict[str, Any]] = {
    "Basic Tier": {
        "display_name": "Vantav Basic",
        "monthly_price": 0,
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
        "monthly_price": 399,
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
        "monthly_price": 1398,
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
        "monthly_price": 399,
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
        "monthly_price": 799,
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
        "display_name": "Vantav Starter",
        "monthly_price": 199,
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
        "monthly_price": 399,
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
        "monthly_price": 799,
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
        "monthly_price": 999,
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

TIER_DESCRIPTIONS = {
    "Basic Tier": {
        "name": "Vantav Basic",
        "price": "Free",
        "who": "For solo operators just getting started",
        "summary": "Core profit visibility and alerts for one store.",
        "features": ["True profit by channel", "Live alerts", "Email support"],
        "all_features": [
            "True profit by channel",
            "Live alerts",
            "Email support",
        ],
        "popular": False,
    },
    "Vantav Operator": {
        "name": "Vantav Starter",
        "price": "$199/mo",
        "who": "For 1 store getting its numbers straight",
        "summary": "Best for one store. Sync orders, inventory, and profit across Shopify, TikTok Shop, and Amazon.",
        "features": [
            "Up to 2 connected stores",
            "All supported ecommerce integrations",
            "True-profit reporting",
            "3 users",
            "Basic inventory forecasting",
        ],
        "all_features": [
            "Up to 2 connected stores",
            "All supported ecommerce integrations",
            "True-profit reporting",
            "Full revenue, cost, margin, refund, and inventory view",
            "Alerts when something needs attention",
            "Clear, actionable recommendations",
            "See the reasoning behind every recommendation",
            "Approve, edit, or skip any suggestion",
            "Basic inventory forecasting",
            "3 users",
            "Standard support",
        ],
        "popular": False,
    },
    "Vantav Growth": {
        "name": "Vantav Growth",
        "price": "$399/mo",
        "who": "For up to 5 stores across several channels",
        "summary": "More stores, seats, and recommendations for growing brands.",
        "features": [
            "Everything in Starter",
            "Up to 5 connected stores",
            "Smarter issue detection",
            "10 users",
            "Priority support",
        ],
        "all_features": [
            "Everything in Starter",
            "Up to 5 connected stores",
            "Updates every 15 minutes",
            "Smarter issue detection",
            "Product sell-through and stockout forecasting",
            "Recommended reorder timing",
            "See projected financial impact of each suggestion",
            "More recommendations each month",
            "Compare performance across stores",
            "Longer historical analysis",
            "Track completed actions and results",
            "Action history & business context",
            "10 users",
            "Priority support",
        ],
        "popular": True,
    },
    "Vantav Scale": {
        "name": "Vantav Scale",
        "price": "$799/mo",
        "who": "For 15+ stores, multi-brand portfolios and agencies",
        "summary": "Command centre for multi-brand portfolios and high-volume teams.",
        "features": [
            "Everything in Growth",
            "Up to 15 connected stores",
            "Portfolio-wide intelligence",
            "API access",
            "Priority onboarding",
            "Priority support",
        ],
        "all_features": [
            "Everything in Growth",
            "Up to 15 connected stores",
            "Portfolio-wide intelligence",
            "Advanced cross-store monitoring",
            "Higher monthly action allowance",
            "Advanced forecasting",
            "Multi-brand reporting",
            "Custom alert rules",
            "Advanced permissions",
            "Complete action / audit history",
            "API and data access",
            "Priority onboarding",
            "Priority support",
        ],
        "popular": False,
    },
}

TIER_PAGE_ACCESS = {
    # Basic Tier
    "overview": "Basic Tier",
    "alerts": "Basic Tier",
    "settings": "Basic Tier",
    "billing": "Basic Tier",
    "support": "Basic Tier",
    "profit_engine": "Basic Tier",
    "action_gate": "Basic Tier",
    # Vantav Operator
    "inventory": "Vantav Operator",
    "orders": "Vantav Operator",
    "products": "Vantav Operator",
    "store_catalog": "Vantav Operator",
    "catalog": "Vantav Operator",
    "commerce_hub": "Vantav Operator",
    "customers": "Vantav Operator",
    # Vantav Growth
    "command_center": "Vantav Growth",
    "monitoring": "Vantav Growth",
    "predictions": "Vantav Growth",
    "health_score": "Vantav Growth",
    "marketing": "Vantav Growth",
    "automations": "Vantav Growth",
    "team_ai": "Vantav Growth",
    "product_research": "Vantav Growth",
    "fulfillment": "Vantav Growth",
    "returns": "Vantav Growth",
    "shipments": "Vantav Growth",
    "suppliers": "Vantav Growth",
    "tiktok_studio": "Vantav Growth",
    "discounts": "Vantav Growth",
    "analytics": "Vantav Growth",
    "mobile": "Vantav Growth",
    # Vantav Scale
    "fraud": "Vantav Scale",
    "startup_pack": "Vantav Scale",
    "apps": "Vantav Scale",
    "reports": "Vantav Scale",
    "regression_chart": "Vantav Scale",
}

DEFAULT_MERCHANT_PAGE_IDS: set = set(TIER_PAGE_ACCESS.keys())


class TierManager:
    """Enforces tenant tier limits and feature flags."""

    @staticmethod
    def get_tier_meta(tier: str) -> Dict[str, Any]:
        return TIER_LIMITS.get(tier, TIER_LIMITS["Basic Tier"])

    @staticmethod
    def get_tier_description(tier: str) -> Dict[str, Any]:
        """Return canonical name, price, summary, features and tagline for a tier."""
        desc = TIER_DESCRIPTIONS.get(tier)
        if desc:
            return desc
        # Fallback to the next lower known tier by rank.
        rank = TierManager.tier_rank(tier)
        fallback_id = None
        fallback_rank = -1
        for tier_id, tier_desc in TIER_DESCRIPTIONS.items():
            r = TierManager.tier_rank(tier_id)
            if r <= rank and r > fallback_rank:
                fallback_rank = r
                fallback_id = tier_id
        return TIER_DESCRIPTIONS.get(fallback_id or "Basic Tier", TIER_DESCRIPTIONS["Basic Tier"])

    @staticmethod
    def get_plan_options(current_tier: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return canonical plan choices, sorted from Basic to Scale."""
        ordered = sorted(
            ((tid, TierManager.tier_rank(tid)) for tid in TIER_DESCRIPTIONS.keys()),
            key=lambda x: x[1],
        )
        options = []
        for tier_id, _ in ordered:
            desc = TierManager.get_tier_description(tier_id)
            options.append({
                "id": tier_id,
                "name": desc["name"],
                "price": desc["price"],
                "who": desc["who"],
                "summary": desc["summary"],
                "description": desc["summary"],
                "features": desc["features"],
                "all_features": desc["all_features"],
                "popular": desc["popular"],
                "is_current": tier_id == current_tier,
            })
        return options

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
    def get_usage(merchant_id: str) -> Dict[str, Any]:
        """Return canonical usage counts and limits for a merchant."""
        if not merchant_id:
            return {}
        try:
            start_of_month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            order_count = UnifiedOrder.query.filter(
                UnifiedOrder.merchant_id == merchant_id,
                UnifiedOrder.created_at >= start_of_month,
            ).count()
            product_count = Product.query.filter_by(merchant_id=merchant_id).count()
            store_count = MerchantChannel.query.filter_by(merchant_id=merchant_id).count()
            user_count = WorkspaceSeat.query.filter_by(merchant_id=merchant_id).count() or 1
            approved_actions = TierManager.current_action_count(merchant_id)
            profile = MerchantProfile.query.get(merchant_id)
            billing = SaaSBilling.query.get(merchant_id)
            tier = (profile.account_tier if profile else None) or (billing.current_plan if billing else None) or "Basic Tier"
            meta = TierManager.get_tier_meta(tier)
            return {
                "orders": {"used": order_count, "limit": meta.get("monthly_order_limit", 500)},
                "stores": {"used": store_count, "limit": meta.get("max_store_connections", 2)},
                "users": {"used": user_count, "limit": meta.get("max_users", 1)},
                "actions": {"used": approved_actions, "limit": meta.get("max_monthly_actions", 50)},
                "products": {"used": product_count, "limit": 10000},
            }
        except Exception:
            return {}

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
    def get_feature_flags(merchant_id: str) -> Dict[str, Any]:
        profile = MerchantProfile.query.get(merchant_id)
        if not profile:
            return {}
        flags = profile.feature_flags
        if not flags:
            return {}
        return dict(flags)

    @staticmethod
    def page_enabled(merchant_id: str, tier: str, page: str) -> bool:
        """Return True if a merchant can access a dashboard page.

        An explicit feature_flag override takes precedence:
        - True allows access regardless of tier.
        - False blocks access regardless of tier.
        If no override exists, the page must be in the default merchant set and
        the merchant's tier must allow it.
        """
        flags = TierManager.get_feature_flags(merchant_id)
        if page in flags:
            return bool(flags[page])
        if page not in DEFAULT_MERCHANT_PAGE_IDS:
            return False
        return TierManager.can_access_page(tier, page)

    @staticmethod
    def set_feature_flag(merchant_id: str, page: str, enabled: bool) -> bool:
        profile = MerchantProfile.query.get(merchant_id)
        if not profile:
            return False
        flags = dict(profile.feature_flags or {})
        if enabled:
            flags[page] = True
        else:
            flags[page] = False
        profile.feature_flags = flags
        db.session.commit()
        return True

    @staticmethod
    def tier_test_account_emails() -> set:
        env_emails = {e.strip().lower() for e in os.environ.get('MERCHANT_TIER_TEST_ACCOUNTS', '').split(',') if e.strip()}
        return env_emails | {'merchant@vantavcommerce.com'}

    @staticmethod
    def is_tier_test_account(email: str) -> bool:
        return (email or '').strip().lower() in TierManager.tier_test_account_emails()

    @staticmethod
    def page_upgrade_target(page: str) -> str:
        return TIER_PAGE_ACCESS.get(page, "Vantav Growth")
