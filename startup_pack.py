"""Startup Pack intake — concierge model. Merchant submits a brief; admin delivers curated suppliers and direction."""
import json
from typing import Dict, Any, List

from models import db, StartupPackProject


def _default_checklist(brand_name: str, niche: str) -> List[Dict[str, Any]]:
    return [
        {"id": "brand_name", "title": "Confirm brand name and domain", "done": False},
        {"id": "logo", "title": f"Create logo and visual identity for {brand_name or 'your brand'}", "done": False},
        {"id": "niche_validate", "title": f"Validate niche: {niche or 'chosen niche'} with 10 ideal-customer interviews", "done": False},
        {"id": "sample_order", "title": "Order samples with branding from chosen supplier", "done": False},
        {"id": "store_build", "title": "Build Shopify store and connect to Vantav", "done": False},
        {"id": "product_listing", "title": "Launch first 5-10 product listings with profit-ready pricing", "done": False},
        {"id": "tiktok_content", "title": "Create TikTok account and post 3-5 organic videos", "done": False},
        {"id": "ad_launch", "title": "Launch first $20/day TikTok or Meta ad test", "done": False},
        {"id": "profit_review", "title": "Review true-profit report after first 10 orders", "done": False},
        {"id": "automation", "title": "Approve first Pending Actions reorder / ad adjustment", "done": False},
        {"id": "milestone_90", "title": "90-day: refine top 2 winning products and double down", "done": False},
        {"id": "milestone_1yr", "title": "1-year: expand to Amazon + wholesale B2B channel", "done": False},
    ]


def get_project(merchant_id: str) -> StartupPackProject:
    project = StartupPackProject.query.filter_by(merchant_id=merchant_id).first()
    if not project:
        project = StartupPackProject(
            merchant_id=merchant_id,
            status="intake",
            checklist=json.dumps(_default_checklist("", "")),
        )
        db.session.add(project)
        db.session.commit()
    return project


def save_intake(merchant_id: str, data: Dict[str, Any]) -> StartupPackProject:
    project = get_project(merchant_id)
    project.brand_name = data.get("brand_name", project.brand_name)
    project.niche = data.get("niche", project.niche)
    project.target_audience = data.get("target_audience", project.target_audience)
    try:
        project.monthly_ad_budget = float(data.get("monthly_ad_budget", 0) or 0)
    except (TypeError, ValueError):
        project.monthly_ad_budget = 0.0
    project.design_vibe = data.get("design_vibe", project.design_vibe)
    project.has_domain = bool(data.get("has_domain", project.has_domain))
    project.sample_product = data.get("sample_product", project.sample_product)
    project.status = "pending_brief"

    brand = project.brand_name or "your brand"
    niche = project.niche or "your niche"
    project.checklist = json.dumps(_default_checklist(brand, niche))
    db.session.commit()
    return project


def deliver_brief(merchant_id: str, brief: str, curated_suppliers: List[Dict[str, Any]], next_steps: str, admin_notes: str = "") -> StartupPackProject:
    project = get_project(merchant_id)
    project.brief = brief
    project.curated_suppliers = json.dumps(curated_suppliers)
    project.next_steps = next_steps
    project.admin_notes = admin_notes
    project.status = "delivered"
    db.session.commit()
    return project


def list_pending_briefs() -> List[StartupPackProject]:
    return StartupPackProject.query.filter(
        StartupPackProject.status.in_(["intake", "pending_brief"])
    ).order_by(StartupPackProject.created_at.desc()).all()


def list_delivered_briefs() -> List[StartupPackProject]:
    return StartupPackProject.query.filter(
        StartupPackProject.status.in_(["delivered", "in_progress", "launched"])
    ).order_by(StartupPackProject.updated_at.desc()).all()


def complete_item(merchant_id: str, item_id: str) -> StartupPackProject:
    project = get_project(merchant_id)
    checklist = json.loads(project.checklist or "[]")
    for item in checklist:
        if item.get("id") == item_id:
            item["done"] = not item.get("done", False)
    project.checklist = json.dumps(checklist)
    db.session.commit()
    return project


def mark_status(merchant_id: str, status: str) -> StartupPackProject:
    project = get_project(merchant_id)
    project.status = status
    db.session.commit()
    return project


def project_to_dict(project: StartupPackProject) -> Dict[str, Any]:
    return {
        "id": project.id,
        "merchant_id": project.merchant_id,
        "brand_name": project.brand_name,
        "niche": project.niche,
        "target_audience": project.target_audience,
        "monthly_ad_budget": project.monthly_ad_budget,
        "design_vibe": project.design_vibe,
        "has_domain": project.has_domain,
        "sample_product": project.sample_product,
        "status": project.status,
        "brief": project.brief,
        "curated_suppliers": json.loads(project.curated_suppliers or "[]"),
        "next_steps": project.next_steps,
        "admin_notes": project.admin_notes,
        "checklist": json.loads(project.checklist or "[]"),
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }
