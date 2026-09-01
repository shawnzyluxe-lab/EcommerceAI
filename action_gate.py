# Copyright (c) 2026 Vantav / Shawnzyluxe. All rights reserved.
# This file is part of the Vantav Commerce Platform and is proprietary software.
# Unauthorized copying, modification, distribution, or use is strictly prohibited.
# See LICENSE for the full proprietary license terms.

"""Actions — human-in-the-loop approval for AI-drafted operations."""
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

from models import db, PendingAction, Alert, PredictiveLogistics, GeneratedPurchaseOrder, ProfitFeedOrder, AdSpendAnalytic, BusinessMetric, MerchantProfile, BusinessMemory, ActionEvidence, Product, Supplier, MarketingCampaign
from tier_manager import TierManager
import alert_matrix
import competitor_intelligence
import outbound
import shopify_sync

logger = logging.getLogger(__name__)


def _now():
    return datetime.utcnow()


def _json(payload: Any) -> str:
    return json.dumps(payload, default=str)


def _parse(payload: str) -> Any:
    return json.loads(payload) if payload else {}


def _resolve_supplier(merchant_id: str, sku: str, fallback_name: str = "Supplier") -> Tuple[str, Optional[str]]:
    """Return supplier name and email for a SKU using Product/Supplier records."""
    product = Product.query.filter_by(sku=sku, merchant_id=merchant_id).first()
    if product and product.supplier_id:
        supplier = Supplier.query.filter_by(id=product.supplier_id, merchant_id=merchant_id).first()
        if supplier:
            return (supplier.name or fallback_name), supplier.email
    return fallback_name, None


def get_business_memory(merchant_id: str) -> BusinessMemory:
    """Return the merchant's business memory guardrails, creating defaults if missing."""
    memory = BusinessMemory.query.filter_by(merchant_id=merchant_id).first()
    if not memory:
        memory = BusinessMemory(merchant_id=merchant_id)
        db.session.add(memory)
        db.session.commit()
    return memory


def verify_action_against_business_memory(proposed_action: Dict[str, Any], memory: BusinessMemory) -> bool:
    """Guardrail check: raise ValueError if the proposed action violates merchant memory."""
    action_kind = proposed_action.get("action_type") or proposed_action.get("kind")
    payload = proposed_action.get("payload") or {}
    sku = payload.get("sku")

    forbidden = set(memory.forbidden_discount_skus or [])
    if action_kind in ("discount", "price_drop") and sku and str(sku) in forbidden:
        raise ValueError(f"Execution Halting: AI proposed a price drop for SKU {sku}, which is blacklisted in business memory.")

    if action_kind == "ad_adjust" or action_kind == "ad_budget":
        proposed_cac = payload.get("projected_cac") or payload.get("cac")
        if proposed_cac is not None:
            max_cac_allowed = float(memory.max_cac_threshold or 18.0)
            if float(proposed_cac) > max_cac_allowed:
                raise ValueError(f"Execution Halting: Projected CAC (${proposed_cac}) violates merchant limit (${max_cac_allowed}).")

    return True


def _projected_cac_for_ad_adjust(merchant_id: str, platform: str) -> Optional[float]:
    """Compute projected CAC for an ad budget adjustment on a platform."""
    ad = AdSpendAnalytic.query.filter_by(merchant_id=merchant_id, platform_source=platform).first()
    if not ad:
        return None
    current_spend = float(ad.current_spend or 0.0)
    conversions = int(ad.conversion_count or 0)
    if conversions <= 0:
        return None
    return round(current_spend / conversions, 2)


def _action_cost_estimate(action_type: str, payload: Dict[str, Any], merchant_id: str) -> float:
    """Estimate the immediate cost/risk exposure of an action."""
    if action_type == "reorder":
        quantity = int(payload.get("quantity", 0) or 0)
        sku = payload.get("sku", "")
        # Estimate unit cost from recent order COGS if available.
        orders = ProfitFeedOrder.query.filter_by(merchant_id=merchant_id).order_by(ProfitFeedOrder.recorded_at.desc()).limit(20).all()
        avg_cogs = 0.0
        if orders:
            total_items = max(sum(o.items or 1 for o in orders), 1)
            avg_cogs = sum(float(o.cost_of_goods_sold or 0.0) for o in orders) / total_items
        unit_cost = avg_cogs if avg_cogs > 0 else 10.0
        return round(quantity * unit_cost, 2)

    if action_type == "refund":
        order_id = payload.get("order_id", "")
        order = ProfitFeedOrder.query.filter_by(order_id=order_id, merchant_id=merchant_id).first()
        if order:
            return float(order.gross_revenue or 0.0)
        return 0.0

    if action_type == "ad_adjust":
        ad = AdSpendAnalytic.query.filter_by(merchant_id=merchant_id, platform_source=payload.get("platform", "")).first()
        if ad and ad.budget_allocated:
            return abs(float(ad.budget_allocated or 0.0) * (payload.get("adjustment", 0) or 0) / 100.0)
        return 0.0

    return 0.0


def _action_value_estimate(action_type: str, payload: Dict[str, Any], snapshot: Dict[str, Any]) -> float:
    """Estimate the merchant-value (revenue at risk / recovery) of an action."""
    kpis = (snapshot or {}).get("kpis") or {}
    gross = float(kpis.get("gross_revenue", 0.0) or 0.0)
    if action_type == "refund":
        return gross * 0.05
    if action_type == "reorder":
        quantity = int(payload.get("quantity", 0) or 0)
        unit_price = gross / max(kpis.get("orders", 1), 1) if gross else 10.0
        return round(quantity * unit_price, 2)
    return gross


def _merchant_approval_stats(merchant_id: str, action_type: str, key: Optional[str] = None) -> Dict[str, Any]:
    """Return historical approval/denial stats for this merchant and action type."""
    q = PendingAction.query.filter(
        PendingAction.merchant_id == merchant_id,
        PendingAction.action_type == action_type,
        PendingAction.status.in_(("approved", "denied", "executed")),
    )
    rows = q.all()
    if not rows:
        return {"total": 0, "approved": 0, "denied": 0, "rate": 1.0}
    approved = sum(1 for r in rows if r.status in ("approved", "executed"))
    denied = sum(1 for r in rows if r.status == "denied")
    total = len(rows)
    return {
        "total": total,
        "approved": approved,
        "denied": denied,
        "rate": approved / total if total else 1.0,
    }


def _confidence_with_feedback(base_confidence: int, merchant_id: str, action_type: str, payload: Dict[str, Any]) -> int:
    """Adjust confidence score by how often this merchant approves similar actions."""
    stats = _merchant_approval_stats(merchant_id, action_type)
    # Weighted blend: 70% base confidence, 30% historical approval rate.
    adjusted = int(base_confidence * 0.7 + (stats["rate"] * 100) * 0.3)
    return max(0, min(100, adjusted))


def _market_evidence_for_action(action_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Gather competitor/market evidence for an action's SKU or platform."""
    sku = payload.get("sku", "")
    platform = payload.get("platform", "")
    if sku:
        return competitor_intelligence.get_market_evidence(sku, platform)
    return {}


def _autopilot_should_execute(action_type: str, payload: Dict[str, Any], memory: BusinessMemory, merchant_id: str, snapshot: Optional[Dict[str, Any]]) -> bool:
    """Return True if the action is safe enough to execute without human approval."""
    if not memory.autopilot_enabled:
        return False

    # Required-approval types always stop for a human.
    required = set(memory.required_approval_action_types or [])
    if action_type in required:
        return False

    # Only auto-execute explicitly allowed types.
    allowed = set(memory.auto_approve_action_types or [])
    if action_type not in allowed:
        return False

    cost = _action_cost_estimate(action_type, payload, merchant_id)
    value = _action_value_estimate(action_type, payload, snapshot or {})
    max_cost = float(memory.autopilot_max_action_cost or 0.0)
    max_value = float(memory.autopilot_max_order_value or 0.0)

    if max_cost > 0 and cost > max_cost:
        return False
    if max_value > 0 and value > max_value:
        return False

    # Extra safety: ad actions must not blow the CAC cap.
    if action_type == "ad_adjust":
        cac = payload.get("projected_cac") or payload.get("cac")
        if cac and float(cac) > float(memory.max_cac_threshold or 18.0):
            return False

    # Historical feedback safety: if merchant denies this type >50%, don't autopilot.
    stats = _merchant_approval_stats(merchant_id, action_type)
    if stats["total"] >= 3 and stats["rate"] < 0.5:
        return False

    return True


def create_action(merchant_id: str, action_type: str, title: str, detail: str, payload: Dict[str, Any], alert_id: Optional[int] = None, snapshot: Optional[Dict[str, Any]] = None) -> PendingAction:
    """Create a pending action after guardrail verification, attach evidence, and optionally auto-execute."""
    proposed = {
        "action_type": action_type,
        "payload": payload,
    }
    memory = get_business_memory(merchant_id)
    verify_action_against_business_memory(proposed, memory)

    if action_type == "ad_adjust":
        platform = payload.get("platform", "")
        cac = _projected_cac_for_ad_adjust(merchant_id, platform)
        if cac is not None and "projected_cac" not in payload:
            payload = dict(payload)
            payload["projected_cac"] = cac

    action = PendingAction(
        merchant_id=merchant_id,
        alert_id=alert_id,
        action_type=action_type,
        title=title,
        detail=detail,
        payload=_json(payload),
    )
    db.session.add(action)
    db.session.flush()
    evidence = _attach_evidence(action, merchant_id, snapshot=snapshot)

    # Autopilot: if safe, execute immediately.
    if _autopilot_should_execute(action_type, _parse(action.payload), memory, merchant_id, snapshot):
        logger.info(f"[Actions] Autopilot executing action {action.id} for {merchant_id}")
        before = snapshot or _capture_snapshot(merchant_id)
        result = _execute_action(action, _parse(action.payload), evidence=evidence)
        action.status = "executed"
        action.decided_at = _now()
        action.decision_by = "autopilot"
        action.result_summary = result["message"]
        _record_execution_report(action, evidence, before)
        if action.alert_id:
            alert = Alert.query.get(action.alert_id)
            if alert and alert.status == "open":
                alert.status = "resolved"
                alert.resolved_at = _now()
        db.session.commit()

    return action


def _capture_snapshot(merchant_id: str) -> Dict[str, Any]:
    try:
        import agent_context
        return agent_context.get_snapshot(merchant_id)
    except Exception:
        return {}


def _record_execution_report(action: PendingAction, evidence: ActionEvidence, before: Dict[str, Any]) -> None:
    """Capture before/after metrics and a human-readable execution report."""
    after = _capture_snapshot(action.merchant_id)
    kpis = before.get("kpis") or {}
    existing = evidence.before_metrics or {}
    if not isinstance(existing, dict):
        existing = {}
    evidence.before_metrics = {**existing, **kpis}
    evidence.after_metrics = after.get("kpis") or {}

    before_net = float((before.get("kpis") or {}).get("net_profit", 0.0) or 0.0)
    after_net = float((after.get("kpis") or {}).get("net_profit", 0.0) or 0.0)
    delta = round(after_net - before_net, 2)
    evidence.execution_report = (
        f"Autopilot executed: {action.result_summary}. "
        f"Net profit moved from ${before_net:,.2f} to ${after_net:,.2f} ({delta:+.2f})."
    )
    db.session.commit()


def verify_action(action_id: int, merchant_id: Optional[str] = None) -> Dict[str, Any]:
    """Re-capture metrics for an already-executed action and produce a verification report."""
    action = get_action(action_id, merchant_id)
    if not action:
        raise ValueError("Action not found")
    if action.status not in ("approved", "executed"):
        raise ValueError(f"Action is {action.status}; verification requires approved or executed")

    evidence = ActionEvidence.query.filter_by(action_id=action.id).first()
    if not evidence:
        raise ValueError("No evidence record for action")

    before = evidence.before_metrics or {}
    after = _capture_snapshot(action.merchant_id)
    evidence.after_metrics = after.get("kpis") or {}

    before_net = float((before or {}).get("net_profit", 0.0) or 0.0)
    after_net = float((after.get("kpis") or {}).get("net_profit", 0.0) or 0.0)
    delta = round(after_net - before_net, 2)

    evidence.verified_at = _now()
    evidence.verification_report = (
        f"Verified after execution: {action.result_summary}. "
        f"Net profit moved from ${before_net:,.2f} to ${after_net:,.2f} ({delta:+.2f})."
    )
    db.session.commit()

    return {
        "status": "verified",
        "action_id": action.id,
        "verified_at": evidence.verified_at.isoformat() if evidence.verified_at else None,
        "report": evidence.verification_report,
        "before_metrics": evidence.before_metrics,
        "after_metrics": evidence.after_metrics,
    }


def verify_overdue_actions(merchant_id: Optional[str] = None, hours: int = 48) -> List[Dict[str, Any]]:
    """Run verification on executed actions older than the threshold that have not been verified."""
    since = _now() - timedelta(hours=hours)
    q = PendingAction.query.filter(
        PendingAction.status.in_(("approved", "executed")),
        PendingAction.decided_at <= since,
    )
    if merchant_id:
        q = q.filter_by(merchant_id=merchant_id)

    results = []
    for action in q.all():
        evidence = ActionEvidence.query.filter_by(action_id=action.id).first()
        if not evidence or evidence.verified_at:
            continue
        try:
            results.append(verify_action(action.id, merchant_id))
        except Exception as e:
            logger.warning(f"[Actions] Verification failed for action {action.id}: {e}")
    return results


def _attach_evidence(action: PendingAction, merchant_id: str, snapshot: Optional[Dict[str, Any]] = None, confidence: int = 82) -> ActionEvidence:
    """Attach an evidence record to a pending action using live merchant snapshot and market data."""
    if snapshot is None:
        snapshot = _capture_snapshot(merchant_id)
    kpis = (snapshot or {}).get("kpis") or {}
    payload = _parse(action.payload)

    market = _market_evidence_for_action(action.action_type, payload)
    base_confidence = _confidence_with_feedback(confidence, merchant_id, action.action_type, payload)

    telemetry = {
        "conversion_rate_delta": 0.0,
        "competitor_median_price": market.get("competitor_median_price", 0.0),
        "sales_velocity_delta": market.get("sales_velocity_delta", 0.0),
        "historical_trend_days": 14,
        "margin": kpis.get("net_margin", 0.0),
        "orders": kpis.get("orders", 0),
        "gross_revenue": kpis.get("gross_revenue", 0.0),
        "market_trend": market.get("market_trend", "flat"),
        "price_gap_pct": market.get("price_gap_pct", 0.0),
    }

    expected_min, expected_max = _estimate_impact(action.action_type, payload, kpis)

    reasoning = f"AI evaluated {action.title} against live margin, orders, channel telemetry, and market data."
    if market:
        reasoning += f" Market: {market['market_trend']} ({market['sales_velocity_delta']:+.1f}% velocity)."

    evidence = ActionEvidence(
        action_id=action.id,
        merchant_id=merchant_id,
        confidence_score=base_confidence,
        expected_weekly_impact_min=expected_min,
        expected_weekly_impact_max=expected_max,
        telemetry_evidence_log=telemetry,
        reasoning_summary=reasoning,
    )
    db.session.add(evidence)
    db.session.commit()
    return evidence


def _estimate_impact(action_type: str, payload: Dict[str, Any], kpis: Dict[str, Any]) -> tuple:
    expected_min = 0.0
    expected_max = 0.0
    gross = float(kpis.get("gross_revenue", 0.0) or 0.0)
    ad_spend = float(kpis.get("ad_spend", 0.0) or 0.0)
    adj = payload.get("adjustment")

    if action_type == "ad_adjust" and isinstance(adj, (int, float)):
        change = abs(adj) / 100.0
        expected_min = round(ad_spend * change * 0.5, 2)
        expected_max = round(ad_spend * change, 2)
    elif action_type == "reorder":
        quantity = int(payload.get("quantity", 0) or 0)
        velocity = float(payload.get("velocity") or 38.5)
        unit_price = gross / max(kpis.get("orders", 1), 1) if gross else 10.0
        weekly_units = min(quantity, velocity * 7.0)
        expected_min = round(weekly_units * unit_price * 0.6, 2)
        expected_max = round(weekly_units * unit_price, 2)
    elif action_type == "refund":
        expected_min = round(-gross * 0.05, 2)
        expected_max = round(0.0, 2)

    return expected_min, expected_max


def refresh_actions(merchant_id: str):
    """Sync pending actions from open alerts, applying business-memory guardrails."""
    if not merchant_id:
        return
    open_alerts = alert_matrix.get_alerts(merchant_id)
    for alert in open_alerts:
        if alert.status != "open":
            continue
        existing = PendingAction.query.filter_by(
            merchant_id=merchant_id, alert_id=alert.id, status="pending"
        ).first()
        if existing:
            continue

        action_type, payload, title, detail = _infer_action_from_alert(alert)
        if action_type:
            try:
                create_action(
                    merchant_id=merchant_id,
                    action_type=action_type,
                    title=title or alert.title,
                    detail=detail or alert.detail,
                    payload=payload,
                    alert_id=alert.id,
                )
            except ValueError as e:
                logger.warning(f"[Actions] Guardrail blocked alert action: {e}")


def _infer_action_from_alert(alert: Alert):
    """Map an alert to a draft action."""
    if alert.alert_type == "inventory_runout":
        supplier_name, supplier_email = _resolve_supplier(alert.merchant_id, alert.source_id)
        return "reorder", {
            "sku": alert.source_id,
            "quantity": 240,
            "supplier": supplier_name,
            "supplier_email": supplier_email,
            "lead_days": 6,
            "velocity": 38.5,
        }, f"Reorder {alert.source_id}", alert.detail

    if alert.alert_type == "fraud_risk":
        return "refund", {
            "order_id": alert.source_id,
            "amount": 0.0,
        }, f"Review refund for order {alert.source_id}", alert.detail

    if alert.alert_type == "ad_spend":
        return "ad_adjust", {
            "platform": alert.source_id,
            "adjustment": -20.0,
            "unit": "percent",
        }, f"Reduce {alert.source_id} ad budget", alert.detail

    if alert.alert_type == "low_inventory":
        supplier_name, supplier_email = _resolve_supplier(alert.merchant_id, alert.source_id)
        return "reorder", {
            "sku": alert.source_id,
            "quantity": 450,
            "supplier": supplier_name,
            "supplier_email": supplier_email,
            "lead_days": 6,
            "velocity": 38.5,
        }, f"Create PO for {alert.source_id}", alert.detail

    return None, {}, alert.title, alert.detail


def list_pending_actions(merchant_id: str) -> List[PendingAction]:
    refresh_actions(merchant_id)
    return PendingAction.query.filter_by(
        merchant_id=merchant_id, status="pending"
    ).order_by(PendingAction.created_at.desc()).all()


def list_action_history(merchant_id: str, limit: int = 20) -> List[PendingAction]:
    return PendingAction.query.filter(
        PendingAction.merchant_id == merchant_id,
        PendingAction.status.in_(("approved", "denied", "executed")),
    ).order_by(PendingAction.decided_at.desc()).limit(limit).all()


def get_action(action_id: int, merchant_id: Optional[str] = None) -> Optional[PendingAction]:
    q = PendingAction.query.filter_by(id=action_id)
    if merchant_id:
        q = q.filter_by(merchant_id=merchant_id)
    return q.first()


def approve_action(action_id: int, merchant_id: str, decided_by: str = "merchant") -> Dict[str, Any]:
    action = get_action(action_id, merchant_id)
    if not action:
        raise ValueError("Action not found")
    if action.status != "pending":
        raise ValueError(f"Action already {action.status}")
    if not TierManager.can_execute_action(merchant_id):
        profile = MerchantProfile.query.get(merchant_id)
        tier = profile.account_tier if profile else "Basic Tier"
        limit = TierManager.get_action_limit(tier)
        raise ValueError(f"Monthly approved-action limit reached ({limit}). Upgrade to execute more actions.")

    payload = _parse(action.payload)
    memory = get_business_memory(merchant_id)
    verify_action_against_business_memory(
        {"action_type": action.action_type, "payload": payload}, memory
    )

    evidence = ActionEvidence.query.filter_by(action_id=action.id).first()
    before = _capture_snapshot(merchant_id)
    if evidence:
        evidence.before_metrics = before.get("kpis") or {}

    result = _execute_action(action, payload, evidence=evidence)

    action.status = "approved"
    action.decided_at = _now()
    action.decision_by = decided_by
    action.result_summary = result["message"]
    db.session.commit()

    # Capture after metrics for the report card.
    after = _capture_snapshot(merchant_id)
    if evidence:
        evidence.after_metrics = after.get("kpis") or {}
        before_net = float((before.get("kpis") or {}).get("net_profit", 0.0) or 0.0)
        after_net = float((after.get("kpis") or {}).get("net_profit", 0.0) or 0.0)
        delta = round(after_net - before_net, 2)
        evidence.execution_report = (
            f"Approved action: {action.result_summary}. "
            f"Net profit moved from ${before_net:,.2f} to ${after_net:,.2f} ({delta:+.2f})."
        )
        db.session.commit()

    # Resolve the linked alert if present
    if action.alert_id:
        alert = Alert.query.get(action.alert_id)
        if alert and alert.status == "open":
            alert.status = "resolved"
            alert.resolved_at = _now()
            db.session.commit()

    return {"status": "approved", "action_id": action.id, **result}


def deny_action(action_id: int, merchant_id: str, reason: str = "", decided_by: str = "merchant") -> Dict[str, Any]:
    action = get_action(action_id, merchant_id)
    if not action:
        raise ValueError("Action not found")
    if action.status != "pending":
        raise ValueError(f"Action already {action.status}")

    action.status = "denied"
    action.decided_at = _now()
    action.decision_by = decided_by
    action.result_summary = reason or "Denied by user"
    db.session.commit()
    return {"status": "denied", "action_id": action.id}


def modify_action(action_id: int, merchant_id: str, payload_updates: Dict[str, Any]) -> Dict[str, Any]:
    action = get_action(action_id, merchant_id)
    if not action:
        raise ValueError("Action not found")
    if action.status != "pending":
        raise ValueError("Action already decided")

    payload = _parse(action.payload)
    payload.update(payload_updates)
    action.payload = _json(payload)
    db.session.commit()
    return {"status": "modified", "action_id": action.id, "payload": payload}


def _store_rollback_snapshot(evidence: Optional[ActionEvidence], snapshot: Dict[str, Any]) -> None:
    """Attach a rollback snapshot to the evidence audit record while preserving KPIs."""
    if not evidence:
        return
    existing = evidence.before_metrics or {}
    if not isinstance(existing, dict):
        existing = {}
    existing["rollback_snapshot"] = snapshot
    evidence.before_metrics = existing


def _execute_action(action: PendingAction, payload: Dict[str, Any], evidence: Optional[ActionEvidence] = None) -> Dict[str, Any]:
    merchant_id = action.merchant_id
    action_type = action.action_type
    rollback_snapshot: Dict[str, Any] = {"action_type": action_type}

    if action_type == "reorder":
        sku = payload.get("sku", "UNKNOWN")
        quantity = int(payload.get("quantity", 240))
        supplier = payload.get("supplier", "Supplier")
        supplier_email = payload.get("supplier_email")
        lead_days = int(payload.get("lead_days", 6))
        po_ref = f"PO-{sku.replace(' ', '-')}-{uuid.uuid4().hex[:6].upper()}"

        pl = PredictiveLogistics.query.filter_by(variant_sku=sku).first()
        if pl:
            pl.days_remaining = 30
            pl.status_flag = "HEALTHY"

        po = GeneratedPurchaseOrder(
            po_reference=po_ref,
            merchant_id=merchant_id,
            variant_sku=sku,
            units_ordered=quantity,
            fulfillment_status="PENDING",
        )
        db.session.add(po)

        # Business metric update
        db.session.add(BusinessMetric(
            merchant_id=merchant_id,
            total_unified_balance=0.0,
            true_net_profit=0.0,
            gross_revenue=0.0,
            ai_briefing=f"Actions approved: {po_ref} created for {quantity} units of {sku} from {supplier} ({lead_days}-day lead).",
        ))
        db.session.commit()

        rollback_snapshot.update({
            "po_reference": po_ref,
            "sku": sku,
            "quantity": quantity,
            "supplier": supplier,
            "supplier_email": supplier_email,
            "lead_days": lead_days,
        })
        _store_rollback_snapshot(evidence, rollback_snapshot)

        writeback = outbound.send_supplier_po(
            merchant_id,
            po,
            supplier_name=supplier,
            supplier_email=supplier_email,
            lead_days=lead_days,
        )
        return {
            "message": f"Created {po_ref} for {quantity} units of {sku}.",
            "po_reference": po_ref,
            "writeback": writeback,
        }

    if action_type == "price":
        sku = payload.get("sku", "")
        new_price = float(payload.get("price") or payload.get("new_price") or 0)
        previous_price = None
        for product in shopify_sync.get_products(merchant_id):
            if product.get("sku", "").upper() == sku.upper():
                previous_price = float(product.get("price") or 0)
                break
        if not sku or new_price <= 0:
            return {"message": "Price action requires a SKU and a positive new price."}

        rollback_snapshot.update({
            "sku": sku,
            "previous_price": previous_price,
            "new_price": new_price,
            "channel": "shopify",
        })
        _store_rollback_snapshot(evidence, rollback_snapshot)

        writeback = outbound.dispatch_action("price", merchant_id, {"sku": sku, "price": new_price})
        return {
            "message": f"Price for {sku} updated to ${new_price:,.2f} on connected channels.",
            "writeback": writeback,
        }

    if action_type == "refund":
        order_id = payload.get("order_id", "")
        order = ProfitFeedOrder.query.filter_by(order_id=order_id, merchant_id=merchant_id).first()
        if order:
            rollback_snapshot.update({
                "order_id": order_id,
                "previous_state": order.state,
                "previous_refund": float(order.refund_amount or 0),
                "previous_net": float(order.net_profit or 0),
            })
            _store_rollback_snapshot(evidence, rollback_snapshot)

            order.state = "refunded"
            order.refund_amount = order.gross_revenue
            order.net_profit = -order.marketplace_fees - order.cost_of_goods_sold - order.shipping_costs - order.ad_spend_attributed
            db.session.commit()
            writeback = outbound.dispatch_action("refund", merchant_id, payload)
            return {"message": f"Order {order_id} marked as refunded.", "writeback": writeback}
        writeback = outbound.dispatch_action("refund", merchant_id, payload)
        return {"message": f"Order {order_id} not found; refund logged.", "writeback": writeback}

    if action_type == "ad_adjust":
        platform = payload.get("platform", "")
        campaign_id = payload.get("campaign_id")
        adjustment = float(payload.get("adjustment", -20.0))

        # If a campaign_id is provided, target the granular MarketingCampaign record.
        if campaign_id:
            campaign = MarketingCampaign.query.filter_by(
                merchant_id=merchant_id, external_campaign_id=campaign_id
            ).first()
            if campaign:
                current_budget = float(campaign.daily_budget or 0.0)
                new_budget = max(0.0, current_budget * (1 + adjustment / 100.0))
                campaign.daily_budget = new_budget
                db.session.commit()
                rollback_snapshot.update({
                    "record_type": "campaign",
                    "campaign_id": campaign_id,
                    "platform": platform or campaign.channel,
                    "previous_budget": current_budget,
                    "new_budget": new_budget,
                    "adjustment": adjustment,
                })
                _store_rollback_snapshot(evidence, rollback_snapshot)
                writeback = outbound.ad_platform_update_budget(
                    platform or campaign.channel, merchant_id, new_budget, campaign_id=campaign_id
                )
                return {
                    "message": f"{campaign.campaign_name or campaign_id} ad budget adjusted by {adjustment}% to ${new_budget:,.2f}.",
                    "writeback": writeback,
                }

        ad = AdSpendAnalytic.query.filter_by(merchant_id=merchant_id, platform_source=platform).first()
        if ad:
            current_budget = float(ad.budget_allocated or 0.0)
            new_budget = current_budget * (1 + adjustment / 100.0) if ad.budget_allocated else 0.0
            ad.budget_allocated = max(0.0, new_budget)
            db.session.commit()
            rollback_snapshot.update({
                "record_type": "ad_analytic",
                "platform": platform,
                "ad_analytic_id": ad.id,
                "previous_budget": current_budget,
                "new_budget": new_budget,
                "adjustment": adjustment,
            })
            _store_rollback_snapshot(evidence, rollback_snapshot)
            writeback = outbound.ad_platform_update_budget(platform, merchant_id, new_budget)
            return {"message": f"{platform} ad budget adjusted by {adjustment}% to ${new_budget:,.2f}.", "writeback": writeback}
        writeback = outbound.ad_platform_update_budget(platform, merchant_id, 0.0)
        return {"message": f"{platform} ad record not found; adjustment logged.", "writeback": writeback}

    if action_type == "reroute":
        sku = payload.get("sku", "SZL-VAR-B")
        pl = PredictiveLogistics.query.filter_by(variant_sku=sku).first()
        if pl:
            pl.days_remaining = 30
            pl.status_flag = "HEALTHY"
        db.session.commit()
        return {"message": f"Fulfillment rerouted for {sku}; stock status restored."}

    return {"message": "Unknown action type"}


def action_to_dict(action: PendingAction) -> Dict[str, Any]:
    evidence = {}
    can_rollback = False
    try:
        ae = ActionEvidence.query.filter_by(action_id=action.id).first()
        if ae:
            before_metrics = ae.before_metrics or {}
            can_rollback = (
                action.status in ("approved", "executed")
                and isinstance(before_metrics, dict)
                and bool(before_metrics.get("rollback_snapshot"))
            )
            evidence = {
                "confidence_score": ae.confidence_score,
                "expected_weekly_impact_min": float(ae.expected_weekly_impact_min or 0),
                "expected_weekly_impact_max": float(ae.expected_weekly_impact_max or 0),
                "reasoning_summary": ae.reasoning_summary,
                "telemetry_evidence_log": ae.telemetry_evidence_log or {},
                "before_metrics": before_metrics,
                "after_metrics": ae.after_metrics or {},
                "execution_report": ae.execution_report,
                "verified_at": ae.verified_at.isoformat() if ae.verified_at else None,
                "verification_report": ae.verification_report,
            }
    except Exception:
        pass
    return {
        "id": action.id,
        "merchant_id": action.merchant_id,
        "alert_id": action.alert_id,
        "action_type": action.action_type,
        "title": action.title,
        "detail": action.detail,
        "payload": _parse(action.payload),
        "status": action.status,
        "created_at": action.created_at.isoformat() if action.created_at else None,
        "decided_at": action.decided_at.isoformat() if action.decided_at else None,
        "decision_by": action.decision_by,
        "result_summary": action.result_summary,
        "can_rollback": can_rollback,
        "evidence": evidence,
    }


def rollback_action(action_id: int, merchant_id: str, decided_by: str = "merchant") -> Dict[str, Any]:
    """Revert an executed/approved action using the rollback snapshot captured at execution."""
    action = get_action(action_id, merchant_id)
    if not action:
        raise ValueError("Action not found")
    if action.status not in ("approved", "executed"):
        raise ValueError(f"Action is {action.status}; rollback requires approved or executed")

    evidence = ActionEvidence.query.filter_by(action_id=action.id).first()
    if not evidence:
        raise ValueError("No evidence/audit record for action")

    before_metrics = evidence.before_metrics or {}
    if not isinstance(before_metrics, dict):
        raise ValueError("No rollback metadata available")
    snapshot = before_metrics.get("rollback_snapshot") or {}
    if not snapshot:
        raise ValueError("No rollback snapshot captured for this action")

    action_type = snapshot.get("action_type", action.action_type)
    result_message = "Rolled back action."

    if action_type == "reorder":
        po_ref = snapshot.get("po_reference")
        po = GeneratedPurchaseOrder.query.filter_by(po_reference=po_ref, merchant_id=merchant_id).first()
        if po and po.fulfillment_status != "CANCELLED":
            po.fulfillment_status = "CANCELLED"
            result_message = f"Cancelled purchase order {po_ref}."
        else:
            result_message = f"Purchase order {po_ref} already cancelled or not found."

    elif action_type == "price":
        sku = snapshot.get("sku")
        previous_price = snapshot.get("previous_price")
        if sku and previous_price is not None:
            outbound.shopify_update_price(merchant_id, sku, float(previous_price))
            result_message = f"Restored price for {sku} to ${float(previous_price):,.2f}."

    elif action_type == "refund":
        order_id = snapshot.get("order_id")
        order = ProfitFeedOrder.query.filter_by(order_id=order_id, merchant_id=merchant_id).first()
        if order:
            order.state = snapshot.get("previous_state", order.state)
            order.refund_amount = float(snapshot.get("previous_refund", 0) or 0)
            order.net_profit = float(snapshot.get("previous_net", order.net_profit or 0) or 0)
            result_message = f"Restored order {order_id} to pre-refund state."

    elif action_type == "ad_adjust":
        record_type = snapshot.get("record_type")
        previous_budget = float(snapshot.get("previous_budget", 0) or 0)
        if record_type == "campaign":
            campaign_id = snapshot.get("campaign_id")
            campaign = MarketingCampaign.query.filter_by(
                merchant_id=merchant_id, external_campaign_id=campaign_id
            ).first()
            if campaign:
                campaign.daily_budget = previous_budget
                result_message = f"Restored campaign {campaign_id} budget to ${previous_budget:,.2f}."
        elif record_type == "ad_analytic":
            ad_analytic_id = snapshot.get("ad_analytic_id")
            ad = AdSpendAnalytic.query.filter_by(id=ad_analytic_id, merchant_id=merchant_id).first() if ad_analytic_id else None
            if ad:
                ad.budget_allocated = previous_budget
                result_message = f"Restored {ad.platform_source} budget to ${previous_budget:,.2f}."

    action.status = "rolled_back"
    action.decided_at = _now()
    action.decision_by = decided_by
    action.result_summary = result_message
    db.session.commit()

    # Audit log
    db.session.add(BusinessMetric(
        merchant_id=merchant_id,
        total_unified_balance=0.0,
        true_net_profit=0.0,
        gross_revenue=0.0,
        ai_briefing=f"Action {action.id} rolled back by {decided_by}: {result_message}",
    ))
    db.session.commit()

    return {"status": "rolled_back", "action_id": action.id, "message": result_message}
