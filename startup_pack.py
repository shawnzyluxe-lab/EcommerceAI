"""Startup Pack intake — brand builder, US supplier directory, and 1-year launch checklist."""
import json
from typing import Dict, Any, List

from models import db, StartupPackProject


US_SUPPLIERS = [
    {
        "name": "Printful",
        "location": "California / North Carolina",
        "category": "Print-on-demand",
        "sample": True,
        "branding": True,
        "no_inventory": True,
        "note": "Best for apparel, home goods, and accessories. Ships US in 2-5 days.",
    },
    {
        "name": "Printify",
        "location": "Network / US print providers",
        "category": "Print-on-demand",
        "sample": True,
        "branding": True,
        "no_inventory": True,
        "note": "Wide product catalog; competitive margins with US print nodes.",
    },
    {
        "name": "SPOD (Spreadshirt)",
        "location": "North Carolina",
        "category": "Print-on-demand",
        "sample": True,
        "branding": True,
        "no_inventory": True,
        "note": "Fast US fulfillment, strong streetwear/merch quality.",
    },
    {
        "name": "Apliiq",
        "location": "California",
        "category": "Custom apparel (cut & sew)",
        "sample": True,
        "branding": True,
        "no_inventory": True,
        "note": "Best for fashion-forward Gen Z apparel with labels and patches.",
    },
    {
        "name": "T-Pop",
        "location": "France / EU focus",
        "category": "Print-on-demand",
        "sample": True,
        "branding": True,
        "no_inventory": True,
        "note": "Strong eco/sustainable branding; EU shipping.",
    },
    {
        "name": "Gooten",
        "location": "US / global",
        "category": "Print-on-demand",
        "sample": True,
        "branding": False,
        "no_inventory": True,
        "note": "Good for mugs, phone cases, home decor testing.",
    },
    {
        "name": "Oberlo / DSers alternatives",
        "location": "US suppliers only",
        "category": "Dropship directory",
        "sample": False,
        "branding": False,
        "no_inventory": True,
        "note": "Use only for trending product validation; prioritize US shipping.",
    },
]


def _default_checklist(brand_name: str, niche: str) -> List[Dict[str, Any]]:
    return [
        {"id": "brand_name", "title": "Confirm brand name and domain", "done": False},
        {"id": "logo", "title": f"Create logo and visual identity for {brand_name or 'your brand'}", "done": False},
        {"id": "niche_validate", "title": f"Validate niche: {niche or 'chosen niche'} with 10 ideal-customer interviews", "done": False},
        {"id": "sample_order", "title": "Order samples with branding from chosen supplier", "done": False},
        {"id": "store_build", "title": "Build Shopify store and connect to Prometheus OS", "done": False},
        {"id": "product_listing", "title": "Launch first 5-10 product listings with profit-ready pricing", "done": False},
        {"id": "tiktok_content", "title": "Create TikTok account and post 3-5 organic videos", "done": False},
        {"id": "ad_launch", "title": "Launch first $20/day TikTok or Meta ad test", "done": False},
        {"id": "profit_review", "title": "Review true-profit report after first 10 orders", "done": False},
        {"id": "automation", "title": "Approve first Action Gate reorder / ad adjustment", "done": False},
        {"id": "milestone_90", "title": "90-day: refine top 2 winning products and double down", "done": False},
        {"id": "milestone_1yr", "title": "1-year: expand to Amazon + wholesale B2B channel", "done": False},
    ]


def _filter_suppliers(niche: str, design_vibe: str) -> List[Dict[str, Any]]:
    niche = (niche or "").lower()
    if any(k in niche for k in ("apparel", "fashion", "streetwear", "clothing", "merch")):
        return [s for s in US_SUPPLIERS if s["name"] in ("Printful", "Apliiq", "SPOD")]
    if any(k in niche for k in ("home", "decor", "mug", "phone", "accessory")):
        return [s for s in US_SUPPLIERS if s["name"] in ("Printful", "Printify", "Gooten")]
    if any(k in niche for k in ("eco", "sustainable", "green")):
        return [s for s in US_SUPPLIERS if s["name"] in ("T-Pop", "Printful", "Printify")]
    return US_SUPPLIERS[:5]


def get_project(merchant_id: str) -> StartupPackProject:
    project = StartupPackProject.query.filter_by(merchant_id=merchant_id).first()
    if not project:
        project = StartupPackProject(
            merchant_id=merchant_id,
            checklist=json.dumps(_default_checklist("", "")),
            suppliers=json.dumps(US_SUPPLIERS[:3]),
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
    project.status = "in_progress"

    brand = project.brand_name or "your brand"
    niche = project.niche or "your niche"
    project.checklist = json.dumps(_default_checklist(brand, niche))
    project.suppliers = json.dumps(_filter_suppliers(niche, project.design_vibe or ""))
    db.session.commit()
    return project


def complete_item(merchant_id: str, item_id: str) -> StartupPackProject:
    project = get_project(merchant_id)
    checklist = json.loads(project.checklist or "[]")
    for item in checklist:
        if item.get("id") == item_id:
            item["done"] = not item.get("done", False)
    project.checklist = json.dumps(checklist)
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
        "checklist": json.loads(project.checklist or "[]"),
        "suppliers": json.loads(project.suppliers or "[]"),
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


def get_suppliers(niche: str = "", design_vibe: str = "") -> List[Dict[str, Any]]:
    return _filter_suppliers(niche, design_vibe)
