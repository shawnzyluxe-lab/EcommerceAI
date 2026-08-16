"""Automated initial sync event for newly connected merchant stores.

After a store is authorized, this module runs in a background thread to pull
historical data, run regression + COO diagnostics, and populate alerts / draft
actions so the merchant sees immediate value instead of blank charts.
"""
import logging
from datetime import datetime, timedelta
from threading import Thread
from typing import Any, Dict

from flask import Flask

import coo_agent_mesh
import profit_regression
import shopify_sync
import tiktok_sync
import amazon_sync
from models import db, BusinessMetric, Product

logger = logging.getLogger(__name__)


def _pull_channel_data(merchant_id: str, channel: str) -> Dict[str, Any]:
    """Pull historical orders and catalog from the connected channel."""
    result: Dict[str, Any] = {"channel": channel, "orders_synced": 0, "products_synced": 0, "errors": []}
    try:
        if channel == "shopify":
            sync_result = shopify_sync.sync_shopify(merchant_id)
            result["orders_synced"] = sync_result.get("orders_synced", 0)
            result["products_synced"] = sync_result.get("products_synced", 0)
        elif channel == "tiktok":
            sync_result = tiktok_sync.sync_tiktok(merchant_id)
            result["orders_synced"] = sync_result.get("orders_synced", 0)
            result["products_synced"] = sync_result.get("products_synced", 0)
        elif channel == "amazon":
            sync_result = amazon_sync.sync_amazon(merchant_id)
            result["orders_synced"] = sync_result.get("orders_synced", 0)
            result["products_synced"] = sync_result.get("products_synced", 0)
        else:
            result["errors"].append(f"Unknown channel: {channel}")
    except Exception as e:
        logger.error(f"[Initial Sync] {channel} pull failed for {merchant_id}: {e}")
        result["errors"].append(str(e))
    return result


def _run_diagnostics(merchant_id: str) -> Dict[str, Any]:
    """Run mathematical regression and COO mesh diagnostics for the merchant."""
    result: Dict[str, Any] = {"regression_actions": [], "coo_actions": [], "errors": []}
    try:
        regression_actions = profit_regression.run_regression_for_merchant(
            merchant_id, lookback_days=14, create_actions=True
        )
        result["regression_actions"] = regression_actions
    except Exception as e:
        logger.error(f"[Initial Sync] Regression failed for {merchant_id}: {e}")
        result["errors"].append(f"regression: {e}")

    try:
        coo_actions = coo_agent_mesh.run_diagnostic(
            merchant_id, days=14, create_actions=True
        )
        result["coo_actions"] = coo_actions
    except Exception as e:
        logger.error(f"[Initial Sync] COO diagnostic failed for {merchant_id}: {e}")
        result["errors"].append(f"coo: {e}")
    return result


def _summarize_results(merchant_id: str, pull: Dict[str, Any], diagnostics: Dict[str, Any]) -> str:
    """Build a human-readable AI briefing from the initial sync results."""
    total_actions = len(diagnostics.get("regression_actions", [])) + len(diagnostics.get("coo_actions", []))
    sku_count = Product.query.filter_by(merchant_id=merchant_id).count()
    net_profit = 0.0
    top_sku = None
    try:
        from profit_feed import get_kpis
        kpis = get_kpis(merchant_id, window_days=14)
        net_profit = float((kpis or {}).get("net_profit", 0.0) or 0.0)
        top_sku = (kpis or {}).get("top_sku")
    except Exception:
        pass

    channel = pull.get("channel", "store")
    msg = (
        f"Initial {channel} sync complete. Pulled {pull.get('orders_synced', 0)} orders and "
        f"{pull.get('products_synced', 0)} products over the last 14 days. "
        f"{sku_count} SKUs are now tracked, 14-day net profit is ${net_profit:,.2f}. "
        f"{total_actions} draft action(s) staged."
    )
    if top_sku:
        msg += f" Top performer: {top_sku}."
    return msg


def run_first_onboarding_audit(merchant_id: str, channel: str, app: Flask):
    """Execute the full initial sync audit inside a Flask app context."""
    with app.app_context():
        logger.info(f"[Initial Sync] Triggering 14-day historical pull for {merchant_id} on {channel}")
        pull = _pull_channel_data(merchant_id, channel)
        diagnostics = _run_diagnostics(merchant_id)
        summary = _summarize_results(merchant_id, pull, diagnostics)

        try:
            db.session.add(
                BusinessMetric(
                    merchant_id=merchant_id,
                    total_unified_balance=0.0,
                    true_net_profit=0.0,
                    gross_revenue=0.0,
                    ai_briefing=summary,
                    created_at=datetime.utcnow(),
                )
            )
            db.session.commit()
        except Exception as e:
            logger.error(f"[Initial Sync] Failed to write BusinessMetric for {merchant_id}: {e}")
            db.session.rollback()

        logger.info(
            f"[Initial Sync] Completed for {merchant_id}: {pull.get('orders_synced', 0)} orders, "
            f"{len(diagnostics.get('regression_actions', [])) + len(diagnostics.get('coo_actions', []))} actions"
        )


def start_initial_sync(merchant_id: str, channel: str, app: Flask) -> None:
    """Spin off the heavy initial sync audit in a background daemon thread."""
    t = Thread(
        target=run_first_onboarding_audit,
        args=(merchant_id, channel, app),
        daemon=True,
        name=f"initial-sync-{merchant_id[:8]}-{channel}",
    )
    t.start()
    logger.info(f"[Initial Sync] Background thread started for {merchant_id} on {channel}")
