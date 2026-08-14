"""Vantav Marketing Studio — draft high-conversion copy from product context.

Generates TikTok Shop descriptions, SMS hooks, and email-sequence payloads
and stores them in `generated_marketing_assets` with state='draft' until a
merchant or admin approves them.
"""

import datetime
import uuid
from typing import Dict, List, Optional

from pydantic import BaseModel

from models import db, GeneratedMarketingAsset, Product, MerchantProfile


class ProductMarketingContext(BaseModel):
    sku: str
    title: str
    current_margin: float
    viral_velocity_score: int  # 1-100 based on view/sales spikes
    target_demographic: str = "Gen Z"


class GeneratedAssetDraft(BaseModel):
    asset_id: str
    sku: str
    kind: str  # tiktok_description | sms_blast | email_sequence
    copy_payload: Dict
    state: str = "draft"
    created_at: str


class VantavMarketingStudio:
    """Draft customer-facing marketing assets from product performance signals."""

    @staticmethod
    def generate_viral_campaign_drafts(
        product: ProductMarketingContext,
        merchant_id: Optional[str] = None,
        store_url: Optional[str] = None,
    ) -> List[GeneratedAssetDraft]:
        """Evaluate product metrics and draft copy variants."""
        staged_assets: List[GeneratedAssetDraft] = []
        timestamp = datetime.datetime.utcnow().isoformat()
        link = store_url or "https://vantavcommerce.com"

        # 1. TIKTOK SHOP DESCRIPTION UPGRADE LOGIC
        if product.viral_velocity_score > 70:
            asset_id = f"MKT_TTS_{uuid.uuid4().hex[:6].upper()}"
            staged_assets.append(
                GeneratedAssetDraft(
                    asset_id=asset_id,
                    sku=product.sku,
                    kind="tiktok_description",
                    copy_payload={
                        "optimized_title": f"🔥 {product.title} // Limited Drop",
                        "body_copy": (
                            "The item trending right now. Made for daily wear. "
                            "Tap the cart below to secure yours before it sells out. "
                            "Free shipping for the next 2 hours. "
                            "#fyp #tiktokshop #viral"
                        ),
                        "seo_tags": ["tiktokshop", "trending", "musthave", "styleinspo"],
                        "target_demographic": product.target_demographic,
                    },
                    created_at=timestamp,
                )
            )

        # 2. RETENTION SMS HOOK
        if product.current_margin >= 30.0:
            asset_id = f"MKT_SMS_{uuid.uuid4().hex[:6].upper()}"
            staged_assets.append(
                GeneratedAssetDraft(
                    asset_id=asset_id,
                    sku=product.sku,
                    kind="sms_blast",
                    copy_payload={
                        "message_body": (
                            f"Vantav VIP: The {product.title} is back in stock. "
                            f"Take 15% off with code DROP15 now: {link}"
                        ),
                        "target_segment": "repeat_buyers_high_lcv",
                        "discount_code": "DROP15",
                    },
                    created_at=timestamp,
                )
            )

        # 3. EMAIL SEQUENCE
        if product.viral_velocity_score > 50 or product.current_margin >= 25.0:
            asset_id = f"MKT_EMAIL_{uuid.uuid4().hex[:6].upper()}"
            staged_assets.append(
                GeneratedAssetDraft(
                    asset_id=asset_id,
                    sku=product.sku,
                    kind="email_sequence",
                    copy_payload={
                        "subject_line": f"Back in stock: {product.title}",
                        "body": (
                            f"Your saved item is back. Shop the {product.title} before it sells out again — "
                            f"free shipping for the next 2 hours. Use code DROP15 at checkout."
                        ),
                        "discount_code": "DROP15",
                        "target_segment": "wishlist_and_cart_abandoners",
                    },
                    created_at=timestamp,
                )
            )

        if merchant_id:
            VantavMarketingStudio.save_drafts(merchant_id, staged_assets)

        return staged_assets

    @staticmethod
    def save_drafts(merchant_id: str, drafts: List[GeneratedAssetDraft]) -> None:
        """Persist generated drafts to the database in 'draft' state."""
        for draft in drafts:
            existing = GeneratedMarketingAsset.query.filter_by(asset_id=draft.asset_id).first()
            if existing:
                continue
            asset = GeneratedMarketingAsset(
                merchant_id=merchant_id,
                sku=draft.sku,
                asset_id=draft.asset_id,
                kind=draft.kind,
                copy_payload=draft.copy_payload,
                state=draft.state,
            )
            db.session.add(asset)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def list_drafts(merchant_id: str, state: Optional[str] = None) -> List[Dict]:
        """Return persisted drafts for a merchant, optionally filtered by state."""
        q = GeneratedMarketingAsset.query.filter_by(merchant_id=merchant_id)
        if state:
            q = q.filter_by(state=state)
        return [
            {
                "id": a.id,
                "asset_id": a.asset_id,
                "sku": a.sku,
                "kind": a.kind,
                "copy_payload": a.copy_payload,
                "state": a.state,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in q.order_by(GeneratedMarketingAsset.created_at.desc()).all()
        ]

    @staticmethod
    def update_asset_state(asset_id: str, new_state: str) -> Optional[Dict]:
        """Approve, reject, or mark an asset as sent."""
        asset = GeneratedMarketingAsset.query.filter_by(asset_id=asset_id).first()
        if not asset:
            return None
        asset.state = new_state
        db.session.commit()
        return {
            "asset_id": asset.asset_id,
            "sku": asset.sku,
            "kind": asset.kind,
            "state": asset.state,
        }

    @staticmethod
    def build_context_from_product(
        merchant_id: str,
        sku: str,
        viral_velocity_score: int = 0,
        target_demographic: str = "Gen Z",
    ) -> Optional[ProductMarketingContext]:
        """Build a marketing context from the local product catalog."""
        product = Product.query.filter_by(sku=sku, merchant_id=merchant_id).first()
        if not product:
            return None
        unit_cost = float(product.unit_cost or 0.0)
        # Use a placeholder margin if we can't infer price.
        current_margin = 40.0
        return ProductMarketingContext(
            sku=product.sku,
            title=product.title,
            current_margin=current_margin,
            viral_velocity_score=viral_velocity_score,
            target_demographic=target_demographic,
        )


def generate_and_persist(merchant_id: str, sku: str, viral_velocity_score: int = 0) -> List[Dict]:
    """High-level helper: build context, draft assets, persist, and return dicts."""
    ctx = VantavMarketingStudio.build_context_from_product(merchant_id, sku, viral_velocity_score)
    if not ctx:
        return []
    profile = MerchantProfile.query.filter_by(merchant_id=merchant_id).first()
    store_url = profile.brand_url if profile and getattr(profile, "brand_url", None) else None
    drafts = VantavMarketingStudio.generate_viral_campaign_drafts(ctx, merchant_id, store_url)
    return [d.dict() for d in drafts]
