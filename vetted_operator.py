"""Vetted Operator intake and sandbox lifecycle for the Vantav beta."""
import os
import uuid
import secrets
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from werkzeug.security import generate_password_hash
from sqlalchemy import text

from models import db, MerchantProfile, BetaWaitlistApplication, MerchantMetric, SaaSBilling

DEFAULT_SANDBOX_HOURS = int(os.environ.get("BETA_SANDBOX_HOURS", "48"))


def _now():
    return datetime.utcnow()


def submit_application(email: str, business_name: str = "", monthly_volume: str = "",
                       monthly_ad_spend: str = "", ad_channels: str = "", bottleneck: str = "", selected_plan: str = "",
                       ad_plan_addon: bool = False, add_ons: Optional[List[str]] = None) -> BetaWaitlistApplication:
    """Create or update a beta waitlist application."""
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("Email is required")

    add_ons = list(add_ons or [])
    # Legacy ad_plan_addon field syncs with the add_ons list.
    if ad_plan_addon and 'curated_ad_plan' not in add_ons:
        add_ons.append('curated_ad_plan')
    if 'curated_ad_plan' in add_ons:
        ad_plan_addon = True

    app = BetaWaitlistApplication.query.filter_by(email=email).first()
    if app:
        app.business_name = business_name or app.business_name
        app.monthly_volume = monthly_volume or app.monthly_volume
        app.monthly_ad_spend = monthly_ad_spend or app.monthly_ad_spend
        app.ad_channels = ad_channels or app.ad_channels
        app.bottleneck = bottleneck or app.bottleneck
        app.selected_plan = selected_plan or app.selected_plan
        if add_ons:
            app.add_ons = add_ons
        if ad_plan_addon is not None:
            app.ad_plan_addon = ad_plan_addon
    else:
        app = BetaWaitlistApplication(
            email=email,
            business_name=business_name,
            monthly_volume=monthly_volume,
            monthly_ad_spend=monthly_ad_spend,
            ad_channels=ad_channels,
            bottleneck=bottleneck,
            selected_plan=selected_plan,
            ad_plan_addon=bool(ad_plan_addon),
            add_ons=add_ons,
            status="pending",
        )
        db.session.add(app)
    db.session.commit()
    return app


def list_applications(status: Optional[str] = None) -> List[BetaWaitlistApplication]:
    """Return waitlist applications, optionally filtered by status."""
    q = BetaWaitlistApplication.query
    if status:
        q = q.filter_by(status=status)
    return q.order_by(BetaWaitlistApplication.created_at.desc()).all()


def _generate_temp_password():
    return secrets.token_urlsafe(8)


def _create_merchant_for_application(app: BetaWaitlistApplication, tier: str = "Beta Tier") -> MerchantProfile:
    """Provision a merchant profile for an approved application."""
    merchant_id = f"tenant_{uuid.uuid4().hex[:8]}"
    temp_password = _generate_temp_password()

    profile = MerchantProfile(
        merchant_id=merchant_id,
        business_name=app.business_name or app.email.split("@")[0],
        admin_email=app.email,
        password_hash=generate_password_hash(temp_password, method="pbkdf2:sha256"),
        account_tier=tier,
    )
    db.session.add(profile)
    db.session.flush()

    # Seed minimal merchant metrics so the dashboard is not empty.
    db.session.add(MerchantMetric(
        merchant_id=merchant_id,
        total_unified_balance=0.0,
        true_net_profit=0.0,
        gross_revenue=0.0,
        ai_briefing="Sandbox mode — explore the dashboard with simulated data before connecting live channels.",
    ))
    db.session.add(SaaSBilling(merchant_id=merchant_id, current_plan=tier))
    db.session.commit()

    app.merchant_id = merchant_id
    db.session.commit()
    return profile, temp_password


def approve_to_sandbox(app_id: int, hours: int = DEFAULT_SANDBOX_HOURS) -> Dict[str, Any]:
    """Approve an application to the 48-hour sandbox. Creates merchant and returns temp credentials."""
    app = BetaWaitlistApplication.query.get_or_404(app_id)
    if app.status in ("approved", "rejected"):
        raise ValueError(f"Application already {app.status}")

    if not app.merchant_id:
        profile, temp_password = _create_merchant_for_application(app, tier="Beta Tier")
    else:
        profile = MerchantProfile.query.get_or_404(app.merchant_id)
        temp_password = None  # Existing merchant; do not reset password

    now = _now()
    expires = now + timedelta(hours=hours)

    profile.sandbox_status = "sandbox"
    profile.sandbox_started_at = now
    profile.sandbox_expires_at = expires
    profile.live_access_enabled = 0
    profile.approved_at = now
    db.session.commit()

    app.status = "sandbox"
    app.reviewed_at = now
    db.session.commit()

    return {
        "merchant_id": profile.merchant_id,
        "email": profile.admin_email,
        "temp_password": temp_password,
        "sandbox_expires_at": expires.isoformat(),
    }


def approve_to_live(merchant_id: str) -> None:
    """Grant a sandbox merchant live marketplace access after the vetting period."""
    profile = MerchantProfile.query.get_or_404(merchant_id)
    profile.sandbox_status = "approved"
    profile.live_access_enabled = 1
    profile.approved_at = _now()
    db.session.commit()

    if profile.admin_email:
        app = BetaWaitlistApplication.query.filter_by(email=profile.admin_email).first()
        if app:
            app.status = "approved"
            app.reviewed_at = _now()
            db.session.commit()


def reject_application(app_id: int, notes: str = "") -> None:
    """Reject an application."""
    app = BetaWaitlistApplication.query.get_or_404(app_id)
    app.status = "rejected"
    app.notes = notes
    app.reviewed_at = _now()
    if app.merchant_id:
        profile = MerchantProfile.query.get(app.merchant_id)
        if profile:
            profile.sandbox_status = "rejected"
    db.session.commit()


def is_sandbox_active(merchant: MerchantProfile) -> bool:
    """Return True if the merchant is currently in an active sandbox window."""
    if not merchant or merchant.sandbox_status != "sandbox":
        return False
    return merchant.sandbox_expires_at is not None and merchant.sandbox_expires_at > _now()


def can_access_live(merchant: MerchantProfile) -> bool:
    """Return True if merchant may connect live marketplace credentials."""
    if not merchant:
        return False
    if merchant.live_access_enabled:
        return True
    # Sandbox that has expired still cannot access live until explicitly approved.
    return merchant.sandbox_status == "approved"


def gate_check(merchant_id: str, feature: str = "live_sync") -> Dict[str, Any]:
    """Check whether a merchant is allowed to use a live feature."""
    profile = MerchantProfile.query.get(merchant_id)
    if not profile:
        return {"allowed": False, "reason": "Merchant not found"}
    if can_access_live(profile):
        return {"allowed": True, "reason": "Live access enabled"}
    if is_sandbox_active(profile):
        return {
            "allowed": False,
            "reason": f"Sandbox mode active until {profile.sandbox_expires_at.isoformat()}. Live {feature} is disabled.",
        }
    if profile.sandbox_status == "sandbox" and profile.sandbox_expires_at and profile.sandbox_expires_at <= _now():
        return {
            "allowed": False,
            "reason": "Sandbox period ended. Awaiting admin approval for live access.",
        }
    return {"allowed": False, "reason": f"Vetting required before live {feature} is enabled."}


def application_to_dict(app: BetaWaitlistApplication) -> Dict[str, Any]:
    return {
        "id": app.id,
        "email": app.email,
        "business_name": app.business_name,
        "monthly_volume": app.monthly_volume,
        "monthly_ad_spend": app.monthly_ad_spend,
        "ad_channels": app.ad_channels,
        "bottleneck": app.bottleneck,
        "selected_plan": app.selected_plan,
        "ad_plan_addon": bool(app.ad_plan_addon),
        "add_ons": app.add_ons or [],
        "status": app.status,
        "merchant_id": app.merchant_id,
        "notes": app.notes,
        "created_at": app.created_at.isoformat() if app.created_at else None,
        "reviewed_at": app.reviewed_at.isoformat() if app.reviewed_at else None,
    }
