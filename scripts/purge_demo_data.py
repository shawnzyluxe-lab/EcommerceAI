"""Remove seeded demo/sample rows for a merchant so the dashboard only shows real data.

Only rows that exactly match the values written by the bootstrap seeders in
``app.py``, ``profit_feed.seed_demo_data`` and ``alert_matrix.seed_demo_alerts``
are removed, so genuinely synced marketplace data is never touched.

Usage:
    MERCHANT_ID=merchant_shawn_01 python scripts/purge_demo_data.py [--dry-run]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402
from models import (  # noqa: E402
    ActionEvidence,
    AdSpendAnalytic,
    AdSpendFeed,
    AIAgent,
    AgentMessage,
    Alert,
    PendingAction,
    BusinessMetric,
    GeneratedPurchaseOrder,
    LocalProductCatalog,
    MerchantChannel,
    MerchantMetric,
    PredictiveLogistics,
    ProfitFeedOrder,
    SaaSBilling,
    db,
)

DEMO_PROFIT_ORDER_IDS = ["#1042", "#1041", "#1040", "#1039", "#1038", "#1037"]
DEMO_AD_SPEND = [("meta", 80.0), ("tiktok", 120.0), ("amazon", 40.0)]
DEMO_ALERT_SOURCE_IDS = ["demo:1", "demo:2", "demo:3"]
DEMO_AD_ANALYTICS = ["Shopify Product Ads", "TikTok Video Ads", "Meta Retargeting Loop"]
DEMO_METRIC_TRIPLES = [(20560.00, 1394.00, 4582.00), (1240.00, 410.00, 890.00)]
DEMO_PREDICTIVE_SKUS = ["SZL-VAR-A", "SZL-VAR-B"]
DEMO_CATALOG_PRODUCT_IDS = ["prod_882041", "prod_882042"]
DEMO_STRIPE_CUSTOMER_ID = "cus_R8zX1042"


def purge(merchant_id, dry_run=False):
    removed = {}

    def drop(label, query):
        rows = query.all()
        removed[label] = len(rows)
        if not dry_run:
            for row in rows:
                db.session.delete(row)

    drop(
        "profit_feed_orders",
        ProfitFeedOrder.query.filter(
            ProfitFeedOrder.merchant_id == merchant_id,
            ProfitFeedOrder.order_id.in_(DEMO_PROFIT_ORDER_IDS),
        ),
    )
    for platform, amount in DEMO_AD_SPEND:
        drop(
            f"ad_spend_feed:{platform}",
            AdSpendFeed.query.filter(
                AdSpendFeed.merchant_id == merchant_id,
                AdSpendFeed.platform_source == platform,
                AdSpendFeed.amount == amount,
            ),
        )
    # Draft actions and their evidence reference the alerts they came from, so
    # they have to go before the alerts themselves.
    demo_alert_ids = [
        a.id
        for a in Alert.query.filter(
            Alert.merchant_id == merchant_id,
            Alert.source_id.in_(DEMO_ALERT_SOURCE_IDS),
        ).all()
    ]
    demo_action_ids = [
        p.id for p in PendingAction.query.filter(PendingAction.alert_id.in_(demo_alert_ids)).all()
    ] if demo_alert_ids else []
    if demo_action_ids:
        drop(
            "action_evidence",
            ActionEvidence.query.filter(ActionEvidence.action_id.in_(demo_action_ids)),
        )
        drop(
            "pending_actions",
            PendingAction.query.filter(PendingAction.id.in_(demo_action_ids)),
        )
        db.session.flush()
    drop(
        "alerts",
        Alert.query.filter(
            Alert.merchant_id == merchant_id,
            Alert.source_id.in_(DEMO_ALERT_SOURCE_IDS),
        ),
    )
    drop(
        "ad_spend_analytics",
        AdSpendAnalytic.query.filter(
            AdSpendAnalytic.merchant_id == merchant_id,
            AdSpendAnalytic.platform_source.in_(DEMO_AD_ANALYTICS),
        ),
    )
    drop(
        "generated_purchase_orders",
        GeneratedPurchaseOrder.query.filter(
            GeneratedPurchaseOrder.merchant_id == merchant_id,
            GeneratedPurchaseOrder.po_reference == "PO-SZL-A8F2",
        ),
    )
    drop(
        "predictive_logistics",
        PredictiveLogistics.query.filter(
            PredictiveLogistics.variant_sku.in_(DEMO_PREDICTIVE_SKUS)
        ),
    )
    drop(
        "local_product_catalog",
        LocalProductCatalog.query.filter(
            LocalProductCatalog.shopify_product_id.in_(DEMO_CATALOG_PRODUCT_IDS)
        ),
    )
    drop(
        "ai_agents",
        AIAgent.query.filter(AIAgent.merchant_id == merchant_id),
    )
    drop(
        "agent_messages",
        AgentMessage.query.filter(AgentMessage.merchant_id == merchant_id),
    )
    for balance, profit, revenue in DEMO_METRIC_TRIPLES:
        drop(
            f"business_metrics:{revenue}",
            BusinessMetric.query.filter(
                BusinessMetric.merchant_id == merchant_id,
                BusinessMetric.total_unified_balance == balance,
                BusinessMetric.true_net_profit == profit,
                BusinessMetric.gross_revenue == revenue,
            ),
        )

    zeroed = 0
    for metric in MerchantMetric.query.filter_by(merchant_id=merchant_id).all():
        values = (
            metric.total_unified_balance,
            metric.true_net_profit,
            metric.gross_revenue,
        )
        if values in DEMO_METRIC_TRIPLES:
            zeroed += 1
            if not dry_run:
                metric.total_unified_balance = 0.0
                metric.true_net_profit = 0.0
                metric.gross_revenue = 0.0
                metric.ai_briefing = "Awaiting your first synced orders."
    removed["merchant_metrics_zeroed"] = zeroed

    channels_cleared = 0
    for channel in MerchantChannel.query.filter_by(merchant_id=merchant_id).all():
        if (channel.pending_orders, channel.conversion_rate) in (
            (12, 3.4),
            (4, 2.8),
            (7, 4.1),
        ):
            channels_cleared += 1
            if not dry_run:
                channel.pending_orders = 0
                channel.conversion_rate = 0.0
    removed["merchant_channels_cleared"] = channels_cleared

    billing_cleared = 0
    for billing in SaaSBilling.query.filter_by(merchant_id=merchant_id).all():
        if billing.stripe_customer_id == DEMO_STRIPE_CUSTOMER_ID:
            billing_cleared += 1
            if not dry_run:
                billing.stripe_customer_id = ""
                billing.stripe_subscription_item_id = ""
                billing.metered_usage_units = 0
                billing.accrued_invoice_value = 0.0
    removed["saas_billing_cleared"] = billing_cleared

    if not dry_run:
        db.session.commit()
    return removed


def main():
    merchant_id = os.environ.get("MERCHANT_ID", "merchant_shawn_01")
    dry_run = "--dry-run" in sys.argv
    with app.app_context():
        result = purge(merchant_id, dry_run=dry_run)
    mode = "DRY RUN" if dry_run else "PURGED"
    print(f"[{mode}] merchant={merchant_id}")
    for label, count in sorted(result.items()):
        print(f"  {label}: {count}")


if __name__ == "__main__":
    main()
