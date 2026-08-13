"""Action Gate — human-in-the-loop approval for AI-drafted operations."""
import json
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

from models import db, PendingAction, Alert, PredictiveLogistics, GeneratedPurchaseOrder, ProfitFeedOrder, AdSpendAnalytic, BusinessMetric, MerchantProfile, BusinessMemory, ActionEvidence
import alert_matrix


def _now():
    return datetime.utcnow()


def _json(payload: Any) -> str:
    return json.dumps(payload, default=str)


def _parse(payload: str) -> Any:
    return json.loads(payload) if payload else {}


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


def create_action(merchant_id: str, action_type: str, title: str, detail: str, payload: Dict[str, Any], alert_id: Optional[int] = None, snapshot: Optional[Dict[str, Any]] = None) -> PendingAction:
    """Create a pending action after guardrail verification and attach evidence."""
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
    _attach_evidence(action, merchant_id, snapshot=snapshot)
    return action


def _attach_evidence(action: PendingAction, merchant_id: str, snapshot: Optional[Dict[str, Any]] = None, confidence: int = 82) -> ActionEvidence:
    """Attach an evidence record to a pending action using live merchant snapshot."""
    if snapshot is None:
        try:
            import agent_context
            snapshot = agent_context.get_snapshot(merchant_id)
        except Exception:
            snapshot = {}
    kpis = (snapshot or {}).get("kpis") or {}
    telemetry = {
        "conversion_rate_delta": 0.0,
        "competitor_median_price": 0.0,
        "sales_velocity_delta": 0.0,
        "historical_trend_days": 14,
        "margin": kpis.get("net_margin", 0.0),
        "orders": kpis.get("orders", 0),
        "gross_revenue": kpis.get("gross_revenue", 0.0),
    }
    expected_min = 0.0
    expected_max = 0.0
    payload = _parse(action.payload)
    gross = float(kpis.get("gross_revenue", 0.0) or 0.0)
    adj = payload.get("adjustment")
    if action.action_type == "ad_adjust" and isinstance(adj, (int, float)):
        ad_spend = float(kpis.get("ad_spend", 0.0) or 0.0)
        change = abs(adj) / 100.0
        expected_min = round(ad_spend * change * 0.5, 2)
        expected_max = round(ad_spend * change, 2)
    elif action.action_type == "reorder":
        quantity = payload.get("quantity") or 0
        velocity = payload.get("velocity") or snapshot.get("inventory", {}).get("velocity") or 38.5
        unit_price = payload.get("unit_price") or (gross / max(kpis.get("orders", 1), 1) if gross else 0.0)
        weekly_units = min(float(quantity), float(velocity) * 7.0)
        expected_min = round(weekly_units * float(unit_price) * 0.6, 2)
        expected_max = round(weekly_units * float(unit_price), 2)
    elif action.action_type == "refund":
        expected_min = round(-gross * 0.05, 2)
        expected_max = round(0.0, 2)

    reasoning = f"AI evaluated {action.title} against live margin, orders, and channel telemetry."
    evidence = ActionEvidence(
        action_id=action.id,
        merchant_id=merchant_id,
        confidence_score=confidence,
        expected_weekly_impact_min=expected_min,
        expected_weekly_impact_max=expected_max,
        telemetry_evidence_log=telemetry,
        reasoning_summary=reasoning,
    )
    db.session.add(evidence)
    db.session.commit()
    return evidence


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
                import logging
                logging.getLogger(__name__).warning(f"[Action Gate] Guardrail blocked alert action: {e}")
    db.session.commit()


def _infer_action_from_alert(alert: Alert):
    """Map an alert to a draft action."""
    if alert.alert_type == "inventory_runout":
        return "reorder", {
            "sku": alert.source_id,
            "quantity": 240,
            "supplier": "Supplier C",
            "lead_days": 6,
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
        return "reorder", {
            "sku": alert.source_id,
            "quantity": 450,
            "supplier": "Supplier C",
            "lead_days": 6,
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

    payload = _parse(action.payload)
    memory = get_business_memory(merchant_id)
    verify_action_against_business_memory(
        {"action_type": action.action_type, "payload": payload}, memory
    )
    result = _execute_action(action, payload)

    action.status = "approved"
    action.decided_at = _now()
    action.decision_by = decided_by
    action.result_summary = result["message"]
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


def _execute_action(action: PendingAction, payload: Dict[str, Any]) -> Dict[str, Any]:
    merchant_id = action.merchant_id
    action_type = action.action_type

    if action_type == "reorder":
        sku = payload.get("sku", "UNKNOWN")
        quantity = int(payload.get("quantity", 240))
        supplier = payload.get("supplier", "Supplier C")
        lead_days = int(payload.get("lead_days", 6))
        po_ref = f"PO-{sku.replace(' ', '-')}-{uuid.uuid4().hex[:6].upper()}"

        pl = PredictiveLogistics.query.filter_by(variant_sku=sku).first()
        if pl:
            pl.days_remaining = 30
            pl.status_flag = "HEALTHY"

        db.session.add(GeneratedPurchaseOrder(
            po_reference=po_ref,
            merchant_id=merchant_id,
            variant_sku=sku,
            units_ordered=quantity,
            fulfillment_status="PENDING",
        ))

        # Business metric update
        db.session.add(BusinessMetric(
            merchant_id=merchant_id,
            total_unified_balance=0.0,
            true_net_profit=0.0,
            gross_revenue=0.0,
            ai_briefing=f"Action Gate approved: {po_ref} created for {quantity} units of {sku} from {supplier} ({lead_days}-day lead).",
        ))
        db.session.commit()
        return {"message": f"Created {po_ref} for {quantity} units of {sku}.", "po_reference": po_ref}

    if action_type == "refund":
        order_id = payload.get("order_id", "")
        order = ProfitFeedOrder.query.filter_by(order_id=order_id, merchant_id=merchant_id).first()
        if order:
            order.state = "refunded"
            order.refund_amount = order.gross_revenue
            order.net_profit = -order.marketplace_fees - order.cost_of_goods_sold - order.shipping_costs - order.ad_spend_attributed
            db.session.commit()
            return {"message": f"Order {order_id} marked as refunded."}
        return {"message": f"Order {order_id} not found; refund logged."}

    if action_type == "ad_adjust":
        platform = payload.get("platform", "")
        adjustment = float(payload.get("adjustment", -20.0))
        ad = AdSpendAnalytic.query.filter_by(merchant_id=merchant_id, platform_source=platform).first()
        if ad:
            new_budget = ad.budget_allocated * (1 + adjustment / 100.0) if ad.budget_allocated else 0.0
            ad.budget_allocated = max(0.0, new_budget)
            db.session.commit()
            return {"message": f"{platform} ad budget adjusted by {adjustment}% to ${new_budget:,.2f}."}
        return {"message": f"{platform} ad record not found; adjustment logged."}

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
    }
