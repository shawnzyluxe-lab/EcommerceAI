"""Tool definitions and executor for the merchant assistant."""
import json
import logging
from typing import Any, Dict, List

import action_gate
import agent_context
import alert_matrix
import channels as channels_module
import profit_feed
import shopify_sync
import tiktok_studio
import tiktok_sync
import amazon_sync

logger = logging.getLogger(__name__)

TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_profit_summary",
            "description": "Return high-level profit KPIs and channel breakdown.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_orders",
            "description": "Return the most recent orders with profit and state.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "Max number of orders"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_alerts",
            "description": "Return current alerts for the merchant.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pending_actions",
            "description": "Return pending Action Gate items awaiting approval.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_channels",
            "description": "Return connected marketplace channels and sync status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sync_channel_orders",
            "description": "Pull the latest orders and products from a connected channel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "enum": ["shopify", "tiktok", "amazon"],
                        "description": "Channel to sync",
                    }
                },
                "required": ["platform"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_merchant_data",
            "description": "Search products, orders, and alerts by keyword for grounded answers.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search terms"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_tiktok_hooks",
            "description": "Generate TikTok video hooks from a product description.",
            "parameters": {
                "type": "object",
                "properties": {"product": {"type": "string"}},
                "required": ["product"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_tiktok_weekly_plan",
            "description": "Generate a 7-day TikTok content plan for the merchant.",
            "parameters": {
                "type": "object",
                "properties": {"product": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_action",
            "description": "Approve and execute a pending Action Gate item by ID.",
            "parameters": {
                "type": "object",
                "properties": {"action_id": {"type": "integer"}},
                "required": ["action_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deny_action",
            "description": "Deny a pending Action Gate item by ID.",
            "parameters": {
                "type": "object",
                "properties": {"action_id": {"type": "integer"}, "reason": {"type": "string"}},
                "required": ["action_id"],
            },
        },
    },
]


def execute(merchant_id: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a single tool and return a JSON-serializable result."""
    try:
        if tool_name == "get_profit_summary":
            return {
                "kpis": profit_feed.get_kpis(merchant_id),
                "breakdown": profit_feed.get_profit_breakdown(merchant_id),
            }

        if tool_name == "get_recent_orders":
            return {"orders": profit_feed.get_recent_orders(merchant_id, limit=arguments.get("limit", 20))}

        if tool_name == "get_alerts":
            return {"alerts": [alert_matrix.alert_to_dict(a) for a in alert_matrix.get_alerts(merchant_id, limit=10)]}

        if tool_name == "get_pending_actions":
            return {"actions": [action_gate.action_to_dict(a) for a in action_gate.list_pending_actions(merchant_id)]}

        if tool_name == "get_channels":
            return {"channels": channels_module.list_channels(merchant_id)}

        if tool_name == "sync_channel_orders":
            platform = arguments.get("platform")
            if platform == "shopify":
                result = shopify_sync.sync_shopify(merchant_id)
            elif platform == "tiktok":
                result = tiktok_sync.sync_tiktok(merchant_id)
            elif platform == "amazon":
                result = amazon_sync.sync_amazon(merchant_id)
            else:
                return {"error": "Unknown platform"}
            return {"synced": result}

        if tool_name == "search_merchant_data":
            return {"results": agent_context.search_merchant_data(merchant_id, arguments.get("query", ""))}

        if tool_name == "generate_tiktok_hooks":
            return {"hooks": tiktok_studio.generate_hooks(arguments.get("product", ""))}

        if tool_name == "generate_tiktok_weekly_plan":
            return {"weekly_plan": tiktok_studio.generate_weekly_plan(merchant_id, arguments.get("product", ""))}

        if tool_name == "approve_action":
            result = action_gate.approve_action(arguments["action_id"], merchant_id, decided_by="assistant")
            return {"result": result}

        if tool_name == "deny_action":
            result = action_gate.deny_action(arguments["action_id"], merchant_id, reason=arguments.get("reason", ""), decided_by="assistant")
            return {"result": result}

        return {"error": f"Unknown tool: {tool_name}"}
    except Exception as e:
        logger.error(f"[Assistant Tool] {tool_name} failed for {merchant_id}: {e}")
        return {"error": str(e)}
