"""Customer-facing welcome package and registration invoice email.

Sent immediately after a merchant registers (free Basic account) and again
when a paid plan is activated by Stripe. The email carries a plain-language
invoice block plus the "how Vantav works" package for the merchant's tier.
"""
import logging
import secrets
from datetime import datetime
from typing import Any, Dict, Optional

from flask import render_template

from models import db, MerchantProfile, SaaSBilling
from tier_manager import TIER_LIMITS
import outbound

logger = logging.getLogger(__name__)

APP_URL = "https://vantavcommerce.com"
SUPPORT_ADDRESS = "support@vantavcommerce.com"

HOW_IT_WORKS = [
    ("Connect your stores",
     "Open Settings &rarr; Stores and connect Shopify, TikTok Shop, or Amazon. "
     "The first sync pulls your recent orders, fees, refunds, and inventory."),
    ("Enter your product costs",
     "Add unit cost and reorder point on the Products page. Vantav recalculates "
     "profit on every past and future order that uses that SKU."),
    ("Read your Profit Feed",
     "Every order shows revenue, marketplace fees, cost of goods, shipping, ad "
     "spend, and the true profit left over."),
    ("Watch your alerts",
     "Vantav raises an alert when margin slips, stock is running out, or refunds "
     "spike, so you see the problem before it costs you money."),
    ("Approve the next step",
     "Recommendations arrive in the Action Gate with the reasoning behind them. "
     "Nothing is changed in your store until you approve it."),
]

_BASE_PACKAGE = {
    "Basic Tier": {
        "plan_name": "Vantav Basic",
        "amount_usd": 0.0,
        "cadence": "",
        "tagline": "Your free workspace is live.",
        "includes": [
            "Up to 2 connected stores",
            "True-profit reporting on every order",
            "Alerts when something needs attention",
            "Recommendations you approve, edit, or skip",
            "1 user",
            "Standard support",
        ],
        "next_step": "Upgrade any time from Billing to unlock more stores, faster syncs, and forecasting.",
    },
    "Vantav Operator": {
        "plan_name": "Vantav Operator",
        "amount_usd": 199.0,
        "cadence": "per month",
        "tagline": "Operator is active. Here is how to get value in the first hour.",
        "includes": [
            "Up to 2 connected stores",
            "All supported ecommerce integrations",
            "True-profit reporting",
            "Full revenue, cost, margin, refund, and inventory view",
            "Basic inventory forecasting",
            "3 users",
            "Standard support",
        ],
        "next_step": "Connect your first store, then add unit costs so your profit numbers are exact.",
    },
    "Vantav Growth": {
        "plan_name": "Vantav Growth",
        "amount_usd": 399.0,
        "cadence": "per month",
        "tagline": "Growth is active, including cross-store analysis and forecasting.",
        "includes": [
            "Everything in Operator",
            "Up to 5 connected stores",
            "Updates every 15 minutes",
            "SKU velocity and stockout forecasting with reorder timing",
            "Projected financial impact on every recommendation",
            "Compare performance across stores",
            "Action history and business context",
            "10 users",
            "Priority support",
        ],
        "next_step": "Connect every store you sell on so cross-store comparisons and forecasts are complete.",
    },
    "Vantav Scale": {
        "plan_name": "Vantav Scale",
        "amount_usd": 799.0,
        "cadence": "per month",
        "tagline": "Scale is active with portfolio-wide intelligence and API access.",
        "includes": [
            "Everything in Growth",
            "Up to 15 stores",
            "Portfolio-wide intelligence and multi-brand reporting",
            "Advanced cross-store monitoring and forecasting",
            "Custom alert rules and advanced permissions",
            "Complete action and audit history",
            "API and data access",
            "Priority onboarding and support",
        ],
        "next_step": "Your onboarding specialist will reach out to map your brands and alert rules.",
    },
    "Concierge Bundle": {
        "plan_name": "Concierge Bundle",
        "amount_usd": 999.0,
        "cadence": "one time",
        "tagline": "Your done-for-you launch services are booked.",
        "includes": [
            "Custom brand build and manufacturer match",
            "Curated ad plan for TikTok, Amazon, and Meta",
            "SEO setup and monthly optimization",
            "Email and Klaviyo flow setup",
        ],
        "next_step": "Our team emails you a kickoff questionnaire within one business day.",
    },
}

CONCIERGE_LINE_ITEM = {
    "description": "Concierge Bundle (one-time launch services)",
    "amount_usd": 999.0,
}


def canonical_package_tier(tier: Optional[str]) -> str:
    """Map any stored tier name onto a tier that has a welcome package."""
    name = (tier or "").strip()
    if name in _BASE_PACKAGE:
        return name
    display = (TIER_LIMITS.get(name) or {}).get("display_name", "")
    if display in _BASE_PACKAGE:
        return display
    base = display.split(" + ")[0].strip()
    if base in _BASE_PACKAGE:
        return base
    return "Basic Tier"


def get_package(tier: Optional[str]) -> Dict[str, Any]:
    """Return the welcome package content for a tier, with its usage limits."""
    key = canonical_package_tier(tier)
    package = dict(_BASE_PACKAGE[key])
    meta = TIER_LIMITS.get(key) or TIER_LIMITS["Basic Tier"]
    sync_seconds = int(meta.get("sync_frequency_seconds", 3600))
    package["tier_key"] = key
    package["limits"] = {
        "Connected stores": str(meta.get("max_store_connections", 2)),
        "Users included": str(meta.get("max_users", 1)),
        "Orders tracked each month": f"{int(meta.get('monthly_order_limit', 500)):,}",
        "Recommendations each month": f"{int(meta.get('max_monthly_actions', 50)):,}",
        "Store sync": f"every {max(1, sync_seconds // 60)} minutes",
        "Support": str(meta.get("support_level", "Standard")),
    }
    return package


def build_invoice_number(merchant_id: str) -> str:
    suffix = (merchant_id or "").split("_")[-1][:6].upper() or secrets.token_hex(3).upper()
    return f"VNTV-{datetime.utcnow():%Y%m}-{suffix}-{secrets.token_hex(2).upper()}"


def _money(amount: float) -> str:
    return f"${amount:,.2f}"


def build_email(
    profile: MerchantProfile,
    tier: Optional[str] = None,
    amount_cents: Optional[int] = None,
    paid: bool = False,
    concierge_bundle: bool = False,
    invoice_number: Optional[str] = None,
    invoice_reference: Optional[str] = None,
):
    """Render the welcome package email. Returns (subject, html, text)."""
    package = get_package(tier or profile.account_tier)
    plan_amount = float(package["amount_usd"])
    line_items = [{
        "description": f"{package['plan_name']} subscription" if plan_amount else f"{package['plan_name']} account",
        "detail": package["cadence"] or "no charge",
        "amount": _money(plan_amount),
    }]
    total = plan_amount
    if concierge_bundle:
        line_items.append({
            "description": CONCIERGE_LINE_ITEM["description"],
            "detail": "one time",
            "amount": _money(CONCIERGE_LINE_ITEM["amount_usd"]),
        })
        total += CONCIERGE_LINE_ITEM["amount_usd"]
    if amount_cents is not None:
        total = amount_cents / 100.0

    billing = SaaSBilling.query.get(profile.merchant_id)
    context = {
        "app_url": APP_URL,
        "support_address": SUPPORT_ADDRESS,
        "business_name": profile.business_name or profile.admin_email,
        "admin_email": profile.admin_email,
        "package": package,
        "how_it_works": HOW_IT_WORKS,
        "invoice_number": invoice_number or build_invoice_number(profile.merchant_id),
        "invoice_reference": invoice_reference or "",
        "invoice_date": f"{datetime.utcnow():%B %d, %Y}",
        "line_items": line_items,
        "total": _money(total),
        "paid": paid,
        "status_label": "Paid" if paid else ("No payment due" if total == 0 else "Due on activation"),
        "billing_cycle_end": (billing.billing_cycle_end if billing else "") or "",
    }
    subject = (
        f"Your Vantav invoice and {package['plan_name']} welcome package"
        if paid or total else
        f"Welcome to Vantav — your {package['plan_name']} workspace is live"
    )
    html = render_template("email/welcome_invoice.html", **context)
    text = _plain_text(context)
    return subject, html, text


def _plain_text(ctx: Dict[str, Any]) -> str:
    package = ctx["package"]
    lines = [
        f"Welcome to Vantav, {ctx['business_name']}.",
        "",
        package["tagline"],
        "",
        f"INVOICE {ctx['invoice_number']} — {ctx['invoice_date']} — {ctx['status_label']}",
    ]
    for item in ctx["line_items"]:
        lines.append(f"  {item['description']} ({item['detail']}): {item['amount']}")
    lines += [f"  Total: {ctx['total']}", "", f"YOUR PLAN: {package['plan_name']}", ""]
    lines += [f"  - {inc}" for inc in package["includes"]]
    lines += ["", "WHAT YOUR PLAN ALLOWS", ""]
    lines += [f"  {k}: {v}" for k, v in package["limits"].items()]
    lines += ["", "HOW VANTAV WORKS", ""]
    for index, (title, body) in enumerate(ctx["how_it_works"], start=1):
        lines.append(f"  {index}. {title} — {body.replace('&rarr;', '>')}")
    lines += [
        "",
        package["next_step"],
        "",
        f"Open your dashboard: {ctx['app_url']}/dashboard",
        f"Questions: {ctx['support_address']}",
        "",
        "Vantav LLC — Terms: {0}/terms · Privacy: {0}/privacy · Refunds: {0}/refund".format(ctx["app_url"]),
    ]
    return "\n".join(lines)


def send_welcome_package(
    merchant_id: str,
    tier: Optional[str] = None,
    amount_cents: Optional[int] = None,
    paid: bool = False,
    concierge_bundle: bool = False,
    invoice_reference: Optional[str] = None,
) -> Dict[str, Any]:
    """Email the invoice + welcome package to a merchant. Never raises."""
    try:
        profile = MerchantProfile.query.get(merchant_id)
        if not profile or not profile.admin_email:
            return {"status": "skipped", "reason": "No merchant email"}
        subject, html, text = build_email(
            profile,
            tier=tier,
            amount_cents=amount_cents,
            paid=paid,
            concierge_bundle=concierge_bundle,
            invoice_reference=invoice_reference,
        )
        sent = outbound.send_transactional_email(profile.admin_email, subject, html, text_body=text)
        logger.info(f"[WelcomePack] {merchant_id} -> {profile.admin_email}: {'sent' if sent else 'not sent'}")
        return {"status": "ok" if sent else "failed", "to": profile.admin_email}
    except Exception as e:
        db.session.rollback()
        logger.error(f"[WelcomePack] Failed for {merchant_id}: {e}")
        return {"status": "failed", "reason": str(e)}
