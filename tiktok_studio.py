"""TikTok Demand Studio — planning, hooks, creator briefs, and post queue."""
import json
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List

from models import db, MerchantSetting

DEFAULT_TRENDING_SOUNDS = [
    {"name": "original sound - tazzy", "growth": 215, "videos": "125K"},
    {"name": "day in my life - speed up", "growth": 142, "videos": "98K"},
    {"name": "glow up check", "growth": 118, "videos": "84K"},
    {"name": "unboxing things", "growth": 96, "videos": "62K"},
]

DEFAULT_TRENDING_HASHTAGS = [
    {"tag": "#TikTokMadeMeBuyIt", "growth": 168, "views": "3.2B"},
    {"tag": "#SmallBusiness", "growth": 92, "videos": "1.1B"},
    {"tag": "#FoundItOnAmazon", "growth": 74, "videos": "890M"},
    {"tag": "#ShopWithMe", "growth": 61, "videos": "420M"},
]

HOOK_TEMPLATES = [
    "Stop scrolling if you hate {pain}.",
    "I wish I knew this before I started {activity}.",
    "POV: you just discovered the {benefit}.",
    "This changed everything for my {audience}.",
    "The lazy {audience}'s secret to {benefit}.",
    "If you sell {product}, stop doing this.",
    "Nobody talks about this {audience} hack.",
    "I bought this {product} so you don't have to.",
    "How I went from {before} to {after} with one {product}.",
    "If you have {audience}, you need this.",
]


def _load_setting(merchant_id: str, key: str) -> Any:
    setting = MerchantSetting.query.filter_by(merchant_id=merchant_id, setting_key=key).first()
    if setting and setting.setting_value:
        try:
            return json.loads(setting.setting_value)
        except json.JSONDecodeError:
            return None
    return None


def _save_setting(merchant_id: str, key: str, value: Any) -> None:
    setting = MerchantSetting.query.filter_by(merchant_id=merchant_id, setting_key=key).first()
    if not setting:
        setting = MerchantSetting(merchant_id=merchant_id, setting_key=key)
        db.session.add(setting)
    setting.setting_value = json.dumps(value)
    db.session.commit()


def generate_hooks(product_description: str) -> List[str]:
    """Generate a handful of TikTok-style hooks from a product description."""
    desc = (product_description or "").strip()
    if not desc:
        return ["Show your product in action in the first 2 seconds.", "What problem does your product solve? Start there.", "Use a trending sound and show the before/after."]

    words = desc.lower().split()
    product = desc.split()[0] if words else "this product"
    audience = words[-1] if len(words) > 1 else "customers"
    benefit = "better result"
    pain = "wasting time"
    activity = "selling online"
    before = "zero sales"
    after = "consistent orders"

    ctx = {
        "product": product,
        "audience": audience,
        "benefit": benefit,
        "pain": pain,
        "activity": activity,
        "before": before,
        "after": after,
    }

    selected = random.sample(HOOK_TEMPLATES, min(len(HOOK_TEMPLATES), 5))
    return [t.format(**ctx) for t in selected]


def _state(merchant_id: str) -> Dict[str, Any]:
    return _load_setting(merchant_id, "tiktok_studio") or {}


def get_state(merchant_id: str) -> Dict[str, Any]:
    """Return the merchant's TikTok Demand Studio state."""
    state = _state(merchant_id)
    if not state.get("weekly_plan"):
        state["weekly_plan"] = generate_weekly_plan(merchant_id)
    state.setdefault("briefs", [])
    state.setdefault("post_queue", [])
    state.setdefault("trending_sounds", DEFAULT_TRENDING_SOUNDS)
    state.setdefault("trending_hashtags", DEFAULT_TRENDING_HASHTAGS)
    state.setdefault("performance", {
        "views": "1.2M",
        "revenue": "$48.7K",
        "chart": [
            {"day": "Mon", "views": 32, "revenue": 12},
            {"day": "Tue", "views": 45, "revenue": 18},
            {"day": "Wed", "views": 39, "revenue": 15},
            {"day": "Thu", "views": 62, "revenue": 24},
            {"day": "Fri", "views": 58, "revenue": 22},
            {"day": "Sat", "views": 80, "revenue": 31},
            {"day": "Sun", "views": 74, "revenue": 29},
        ],
    })
    return state


def generate_weekly_plan(merchant_id: str, product_description: str = "") -> List[Dict[str, Any]]:
    """Create a 7-day content plan."""
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    states = ["Draft", "Draft", "Scheduled", "Draft", "Scheduled", "Live", "Draft"]
    hooks = generate_hooks(product_description or "your product")
    plan = []
    base = datetime.utcnow()
    for i, day in enumerate(days):
        plan.append({
            "day": day,
            "date": (base + timedelta(days=i - base.weekday())).strftime("%b %d"),
            "status": states[i],
            "hook": hooks[i % len(hooks)],
            "format": random.choice(["Product demo", "UGC reaction", "Trend remix", "Live clip", "Before/after"]),
        })
    state = _state(merchant_id)
    state["weekly_plan"] = plan
    _save_setting(merchant_id, "tiktok_studio", state)
    return plan


def save_brief(merchant_id: str, product_angle: str, niche: str, cta: str) -> Dict[str, Any]:
    """Save a creator brief."""
    state = _state(merchant_id) or {}
    briefs = state.setdefault("briefs", [])
    brief = {
        "id": f"brief_{len(briefs) + 1}",
        "product_angle": product_angle,
        "niche": niche,
        "cta": cta,
        "created_at": datetime.utcnow().isoformat(),
    }
    briefs.append(brief)
    state["briefs"] = briefs
    _save_setting(merchant_id, "tiktok_studio", state)
    return brief


def add_post(merchant_id: str, caption: str, scheduled_for: str, platform: str = "tiktok") -> Dict[str, Any]:
    """Queue a post."""
    state = _state(merchant_id) or {}
    queue = state.setdefault("post_queue", [])
    post = {
        "id": f"post_{len(queue) + 1}",
        "caption": caption,
        "scheduled_for": scheduled_for,
        "platform": platform,
        "created_at": datetime.utcnow().isoformat(),
    }
    queue.append(post)
    state["post_queue"] = queue
    _save_setting(merchant_id, "tiktok_studio", state)
    return post
