import re
from pydantic import BaseModel, HttpUrl
from typing import List, Dict, Any


class ShopifyProductLayout(BaseModel):
    title: str
    description_html: str
    variants: List[Dict[str, Any]]
    images: List[HttpUrl]
    price: float


class TikTokShopDraftPayload(BaseModel):
    product_name: str
    description_clean_text: str
    skus: List[Dict[str, Any]]
    image_ids: List[str]
    category_id: str = "120098"


class ProductTransformerEngine:
    @staticmethod
    def transform_shopify_to_tiktok(source: ShopifyProductLayout) -> TikTokShopDraftPayload:
        """Strip HTML, truncate titles, and build a TikTok-compatible product draft."""
        clean_text = re.sub('<[^<]+?>', '', source.description_html)
        return TikTokShopDraftPayload(
            product_name=source.title[:120],
            description_clean_text=clean_text,
            skus=[{"price": source.price, "seller_sku": v.get("sku")} for v in source.variants],
            image_ids=[str(url) for url in source.images]
        )
