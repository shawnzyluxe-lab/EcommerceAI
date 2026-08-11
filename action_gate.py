"""Action Gate — human-in-the-loop approval for AI-drafted operations."""
import json
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

from models import db, PendingAction, Alert, PredictiveLogistics, GeneratedPurchaseOrder, ProfitFeedOrder, AdSpendAnalytic, BusinessMetric, MerchantProfile
import alert_matrix


def _now():
    return datetime.utcnow()


def _json(payload: Any) -> str:
    return json.dumps(payload, default=str)


def _parse(payload: str) -> Any:
    return json.loads(payload) if payload else {}


def refresh_actions(merchant_id: str):
    """Sync pending actions from open alerts."""
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
            db.session.add(PendingAction(
                merchant_id=merchant_id,
                alert_id=alert.id,
                action_type=action_type,
                title=title or alert.title,
                detail=detail or alert.detail,
                payload=_json(payload),
            ))
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
