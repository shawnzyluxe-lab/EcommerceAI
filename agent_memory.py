"""Threaded conversation memory for the merchant assistant."""
import json
import time
from typing import Dict, List, Any, Optional

from models import db, MerchantSetting

MAX_THREAD_MESSAGES = 40
THREAD_KEY = "assistant_thread"


def _load(merchant_id: str) -> Dict[str, Any]:
    setting = MerchantSetting.query.filter_by(merchant_id=merchant_id, setting_key=THREAD_KEY).first()
    if not setting or not setting.setting_value:
        return {"messages": []}
    try:
        return json.loads(setting.setting_value)
    except json.JSONDecodeError:
        return {"messages": []}


def _save(merchant_id: str, thread: Dict[str, Any]) -> None:
    setting = MerchantSetting.query.filter_by(merchant_id=merchant_id, setting_key=THREAD_KEY).first()
    if not setting:
        setting = MerchantSetting(merchant_id=merchant_id, setting_key=THREAD_KEY)
        db.session.add(setting)
    # Keep only the last N messages to avoid unbounded growth.
    setting.setting_value = json.dumps({"messages": thread["messages"][-MAX_THREAD_MESSAGES:]})
    db.session.commit()


def get_messages(merchant_id: str) -> List[Dict[str, Any]]:
    return _load(merchant_id)["messages"]


def append(
    merchant_id: str,
    role: str,
    content: str,
    name: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> None:
    thread = _load(merchant_id)
    message: Dict[str, Any] = {"role": role, "content": content, "ts": int(time.time())}
    if name:
        message["name"] = name
    if tool_call_id:
        message["tool_call_id"] = tool_call_id
    if tool_calls:
        message["tool_calls"] = tool_calls
    thread["messages"].append(message)
    _save(merchant_id, thread)


def clear(merchant_id: str) -> None:
    setting = MerchantSetting.query.filter_by(merchant_id=merchant_id, setting_key=THREAD_KEY).first()
    if setting:
        setting.setting_value = json.dumps({"messages": []})
        db.session.commit()
