#!/usr/bin/env python3
"""Vantav COO engine background worker.

Continuously refreshes pending actions from open alerts and verifies executed
action outcomes. This is the local/background equivalent of the multi-agent
coordination loop.

Usage:
    python scripts/coo_engine_worker.py
"""

import logging
import os
import time

from app import app
from models import db, MerchantProfile
from action_gate import refresh_actions, verify_executed_actions
import tenant_rls

logger = logging.getLogger("vantav_coo_engine")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())

INTERVAL_SECONDS = int(os.environ.get("COO_ENGINE_INTERVAL", "60"))


def run_coo_cycle() -> None:
    merchants = db.session.query(MerchantProfile.merchant_id).all()
    for (merchant_id,) in merchants:
        tenant_rls.set_tenant_scope(merchant_id)
        try:
            refresh_actions(merchant_id)
            logger.info(f"[COO Engine] Refreshed actions for {merchant_id}")
        except Exception as e:
            logger.warning(f"[COO Engine] Refresh failed for {merchant_id}: {e}")
            db.session.rollback()

    try:
        verify_executed_actions()
        logger.info("[COO Engine] Verified executed actions")
    except Exception as e:
        logger.warning(f"[COO Engine] Verification failed: {e}")
        db.session.rollback()

    db.session.commit()


def main() -> None:
    with app.app_context():
        logger.info("[COO Engine] Starting Vantav coordination worker...")
        while True:
            start = time.time()
            run_coo_cycle()
            elapsed = time.time() - start
            sleep_for = max(0, INTERVAL_SECONDS - elapsed)
            logger.info(f"[COO Engine] Cycle complete in {elapsed:.2f}s; sleeping {sleep_for:.0f}s.")
            time.sleep(sleep_for)


if __name__ == "__main__":
    main()
