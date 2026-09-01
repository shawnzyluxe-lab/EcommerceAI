# Copyright (c) 2026 Vantav / Shawnzyluxe. All rights reserved.
# This file is part of the Vantav Commerce Platform and is proprietary software.
# Unauthorized copying, modification, distribution, or use is strictly prohibited.
# See LICENSE for the full proprietary license terms.

"""Vantav merchant assistant engine with memory, tools, context, and proactive actions."""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import agent_context
import agent_memory
import agent_tools
import channel_analytics
from models import db, PendingAction

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"


_TOOL_MARKUP_RE = re.compile(r"<[^>]*(?:DSML|tool_calls|invoke name=)[^>]*>", re.IGNORECASE)


def _clean_answer(text: Optional[str]) -> str:
    """Strip leaked tool-call markup so merchants never see raw model syntax."""
    cleaned = _TOOL_MARKUP_RE.sub("", text or "").strip()
    if not cleaned:
        return "Let me pull that up — ask me again with a bit more detail."
    return cleaned


def _active_provider() -> Dict[str, str]:
    """Pick the LLM provider from env. Prefer DeepSeek if explicitly requested or only DeepSeek key is set."""
    provider = os.environ.get("ASSISTANT_PROVIDER", "").lower()
    if provider == "deepseek" or (os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("OPENAI_API_KEY")):
        return {
            "name": "deepseek",
            "key": os.environ.get("DEEPSEEK_API_KEY", ""),
            "base_url": "https://api.deepseek.com",
            "model": os.environ.get("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
        }
    return {
        "name": "openai",
        "key": os.environ.get("OPENAI_API_KEY", ""),
        "base_url": None,
        "model": os.environ.get("ASSISTANT_MODEL", DEFAULT_OPENAI_MODEL),
    }


def _llm_client() -> Any:
    """Return an OpenAI-compatible client for the configured provider."""
    provider = _active_provider()
    key = provider["key"]
    if not key:
        return None
    try:
        from openai import OpenAI
        kwargs = {"api_key": key}
        if provider["base_url"]:
            kwargs["base_url"] = provider["base_url"]
        return OpenAI(**kwargs)
    except Exception as e:
        logger.error(f"[Assistant] Could not initialize {provider['name']} client: {e}")
        return None


def _system_prompt(merchant_id: str) -> str:
    snapshot = agent_context.get_snapshot(merchant_id)
    context = agent_context.format_snapshot(snapshot)
    return (
        "You are Vantav Assistant, a concise, multi-channel e-commerce AI operating inside the Vantav dashboard.\n"
        "Use the provided live merchant context and available tools to answer questions and take action.\n"
        "Always ground answers in real data. If you use a tool, explain what it returned in plain English.\n"
        "When asked to do something (sync, approve, generate hooks, etc.), prefer calling the right tool.\n"
        "Keep answers under 3 sentences unless the user asks for detail.\n\n"
        f"--- Live Context ---\n{context}\n---"
    )


def _call_llm(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, tool_choice: str = "auto") -> Any:
    client = _llm_client()
    if not client:
        return None
    provider = _active_provider()
    model = provider["model"]
    try:
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        return client.chat.completions.create(**kwargs)
    except Exception as e:
        logger.error(f"[Assistant] LLM call failed: {e}")
        return None


def _run_tool_loop(merchant_id: str, messages: List[Dict[str, Any]], max_rounds: int = 2) -> Dict[str, Any]:
    """Run the LLM, execute any tool calls, and return the final assistant message."""
    did: List[str] = []
    for _ in range(max_rounds):
        response = _call_llm(messages, tools=agent_tools.TOOLS)
        if response is None:
            provider = _active_provider()
            if provider["key"]:
                return {"answer": f"{provider['name'].title()} key is configured, but the API request failed (no credits, rate limit, or bad model name). I'm answering from live data instead.", "did": did}
            return {"answer": "I'm not connected to a language model yet. Add OPENAI_API_KEY or DEEPSEEK_API_KEY to enable smart answers.", "did": did}

        choice = response.choices[0]
        message = choice.message

        if not message.tool_calls:
            return {"answer": _clean_answer(message.content), "did": did}

        # Append assistant message with tool_calls
        messages.append({
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ],
        })

        for tc in message.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            logger.info(f"[Assistant Tool] {name}({args})")
            result = agent_tools.execute(merchant_id, name, args)
            did.append(f"{name}({json.dumps(args)})")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": name,
                "content": json.dumps(result),
            })

    # Final summarization call after tools.
    final = _call_llm(messages)
    return {"answer": _clean_answer(final.choices[0].message.content) if final else "Done.", "did": did}


def _margin_answer(merchant_id: str, snap: Dict[str, Any]) -> Optional[str]:
    """Where margin is leaking: per-channel true profit plus the cost lines behind it."""
    try:
        rows = channel_analytics.summarize_channels(merchant_id, days=30)
    except Exception:
        rows = []
    if not rows:
        return None

    ranked = sorted(rows, key=lambda r: r.get("margin_pct", 0))
    worst = ranked[0]
    best = ranked[-1]
    lines = [
        f"Margin by channel over the last 30 days — {worst['channel'].title()} is your weakest at "
        f"{worst['margin_pct']}% (${worst['net_profit']:,.0f} net on ${worst['revenue']:,.0f}), "
        f"while {best['channel'].title()} runs at {best['margin_pct']}%."
    ]
    for row in ranked:
        lines.append(
            f"• {row['channel'].title()}: {row['margin_pct']}% margin · "
            f"${row['net_profit']:,.0f} net on ${row['revenue']:,.0f} across {row['orders']} orders"
        )

    breakdown = snap.get("profit_breakdown") or {}
    costs = [r for r in (breakdown.get("profit_rows") or []) if r.get("kind") == "out"]
    if costs:
        biggest = min(costs, key=lambda r: r.get("amount", 0))
        lines.append(
            f"Biggest cost line: {biggest['label'].lower()} at ${abs(biggest['amount']):,.0f}."
        )

    margin_alerts = [
        a for a in (snap.get("alerts") or [])
        if "margin" in (a.get("title", "") + a.get("detail", "")).lower()
    ][:2]
    for alert in margin_alerts:
        lines.append(f"• {alert.get('title', '')}")

    return "\n".join(lines)


def _inventory_answer(snap: Dict[str, Any]) -> Optional[str]:
    """Stockout risk pulled from the alert matrix and pending reorder actions."""
    stock_alerts = [
        a for a in (snap.get("alerts") or [])
        if any(w in (a.get("title", "") + a.get("detail", "")).lower()
               for w in ["stock", "runs out", "reorder", "inventory"])
    ]
    reorders = [
        a for a in (snap.get("pending_actions") or [])
        if "reorder" in (a.get("title", "") + a.get("action_type", "")).lower()
    ]
    if not stock_alerts and not reorders:
        return None

    lines = ["Inventory risk right now:"]
    for alert in stock_alerts[:3]:
        lines.append(f"• {alert.get('title', '')} — {alert.get('detail', '')}")
    for action in reorders[:2]:
        lines.append(f"• Ready to approve: {action.get('title', '')}")
    return "\n".join(lines)


def _action_answer(snap: Dict[str, Any]) -> Optional[str]:
    """Rank pending actions by expected weekly impact."""
    actions = snap.get("pending_actions") or []
    if not actions:
        return None

    def _impact(action: Dict[str, Any]) -> float:
        evidence = action.get("evidence") or {}
        return abs(float(evidence.get("expected_weekly_impact_max") or 0))

    ranked = sorted(actions, key=_impact, reverse=True)
    top = ranked[0]
    evidence = top.get("evidence") or {}
    lines = [f"Do this first: {top.get('title', '')}"]
    if top.get("detail"):
        lines.append(top["detail"])
    impact = _impact(top)
    confidence = evidence.get("confidence_score")
    facts = []
    if impact:
        facts.append(f"worth about ${impact:,.0f} a week")
    if confidence:
        facts.append(f"{round(float(confidence) * 100 if float(confidence) <= 1 else float(confidence))}% confidence")
    if facts:
        lines.append("Vantav rates it " + " at ".join(facts) + ". Approve it in Pending Actions.")
    else:
        lines.append("Approve it in Pending Actions.")

    if len(ranked) > 1:
        lines.append("Then, in order:")
        for action in ranked[1:4]:
            lines.append(f"• {action.get('title', '')}")
    return "\n\n".join(lines)


def _local_answer(merchant_id: str, message: str) -> Dict[str, Any]:
    """Fallback when no LLM key is available: keyword-driven tool calls and summary."""
    msg = message.lower()
    did: List[str] = []

    # Trigger tools based on keyword patterns.
    if any(w in msg for w in ["sync", "pull", "update orders"]):
        for platform in ["shopify", "tiktok", "amazon"]:
            if platform in msg:
                try:
                    agent_tools.execute(merchant_id, "sync_channel_orders", {"platform": platform})
                    did.append(f"synced {platform}")
                except Exception as e:
                    did.append(f"{platform} sync failed: {e}")
                break

    if any(w in msg for w in ["profit", "margin", "how did i do", "kpi"]):
        did.append("get_profit_summary")

    if any(w in msg for w in ["orders", "sales", "recent"]):
        did.append("get_recent_orders")

    if any(w in msg for w in ["alert", "warning", "issue"]):
        did.append("get_alerts")

    if any(w in msg for w in ["action", "approve", "pending"]):
        did.append("get_pending_actions")

    if any(w in msg for w in ["tiktok hook", "hook", "video idea"]):
        product = message.replace("generate", "").replace("hook", "").replace("for", "").strip()
        if product:
            try:
                agent_tools.execute(merchant_id, "generate_tiktok_hooks", {"product": product})
                did.append("generated tiktok hooks")
            except Exception as e:
                did.append(f"hook generation failed: {e}")

    snap = agent_context.get_snapshot(merchant_id)
    logger.info("[Assistant] No LLM key configured; answering %s from live data only.", merchant_id)

    if any(w in msg for w in ["top action", "what should i do", "do first", "pending action", "approve"]):
        action_answer = _action_answer(snap)
        if action_answer:
            return {"answer": action_answer, "did": did}

    if any(w in msg for w in ["losing margin", "margin", "channel", "profit by", "least profitable"]):
        margin_answer = _margin_answer(merchant_id, snap)
        if margin_answer:
            return {"answer": margin_answer, "did": did}

    if any(w in msg for w in ["stock", "inventory", "reorder", "run out"]):
        inventory_answer = _inventory_answer(snap)
        if inventory_answer:
            return {"answer": inventory_answer, "did": did}

    kpis = snap.get("kpis") or {}
    lines = [f"Here's where {snap.get('business_name', 'your store')} stands right now:"]

    net = kpis.get("net_profit")
    gross = kpis.get("gross_revenue")
    margin = kpis.get("net_margin")
    orders = kpis.get("orders")
    if net is not None or gross is not None:
        lines.append(
            f"True net profit ${net:,.0f} on ${gross:,.0f} revenue "
            f"({margin}% margin, {orders} orders)."
            if isinstance(net, (int, float)) and isinstance(gross, (int, float))
            else f"Net profit {net}, revenue {gross}."
        )

    connected = [c.get("name") for c in (snap.get("channels") or []) if c.get("state") == "connected"]
    if connected:
        lines.append("Connected channels: " + ", ".join(connected) + ".")

    for alert in (snap.get("alerts") or [])[:3]:
        lines.append(f"• {alert.get('severity', '').title()}: {alert.get('title', '')}")

    top_action = (snap.get("pending_actions") or [])[:1]
    if top_action:
        lines.append(f"Recommended next step: {top_action[0].get('title', '')} — approve it in Pending Actions.")

    return {"answer": "\n\n".join(lines), "did": did}


def chat(merchant_id: str, user_message: str) -> Dict[str, Any]:
    """Process a user message, update memory, and return the assistant answer."""
    agent_memory.append(merchant_id, "user", user_message)

    messages = [{"role": "system", "content": _system_prompt(merchant_id)}]
    for m in agent_memory.get_messages(merchant_id):
        # Convert memory format to OpenAI message format.
        msg = {"role": m["role"], "content": m["content"]}
        if "tool_calls" in m:
            msg["tool_calls"] = m["tool_calls"]
        if "tool_call_id" in m:
            msg["tool_call_id"] = m["tool_call_id"]
            msg["name"] = m.get("name", "tool")
        messages.append(msg)

    if _llm_client():
        result = _run_tool_loop(merchant_id, messages)
    else:
        result = _local_answer(merchant_id, user_message)

    agent_memory.append(merchant_id, "assistant", result["answer"])
    return result


def clear_thread(merchant_id: str) -> None:
    agent_memory.clear(merchant_id)


def run_proactive(merchant_id: str) -> List[Dict[str, Any]]:
    """Scan merchant data and create or update recommended actions."""
    import action_gate
    import alert_matrix
    import profit_feed

    action_gate.refresh_actions(merchant_id)
    created: List[Dict[str, Any]] = []

    # Rule-based proactive additions beyond alert-driven actions.
    snap = agent_context.get_snapshot(merchant_id)
    kpis = snap.get("kpis") or {}
    margin = kpis.get("net_margin", 0)
    new_actions: List[PendingAction] = []
    if isinstance(margin, (int, float)) and margin < 15:
        try:
            new_actions.append(action_gate.create_action(
                merchant_id=merchant_id,
                action_type="ad_adjust",
                title="Review low-margin ad spend",
                detail=f"Overall margin is {margin:.1f}%. Consider pausing underperforming campaigns or raising prices.",
                payload={"adjustment": -15.0, "platform": "tiktok"},
                snapshot=snap,
            ))
        except ValueError as e:
            logger.warning(f"[Assistant Proactive] Guardrail blocked rule action: {e}")

    # LLM-based proactive recommendations if available.
    if _llm_client():
        try:
            prompt = (
                "You are a proactive e-commerce AI. Given the merchant snapshot below, "
                "recommend 0-3 concrete actions. Return ONLY a JSON array of objects with keys: "
                "action_type (reorder, refund, ad_adjust, reroute), title, detail, payload.\n\n" +
                agent_context.format_snapshot(snap)
            )
            response = _call_llm([{"role": "user", "content": prompt}], tools=None)
            if response:
                text = response.choices[0].message.content or "[]"
                # Strip markdown code fences if present.
                text = text.strip().strip("`").replace("json\n", "").replace("json", "")
                recommendations = json.loads(text)
                for rec in recommendations:
                    try:
                        action = action_gate.create_action(
                            merchant_id=merchant_id,
                            action_type=rec.get("action_type", "reorder"),
                            title=rec.get("title", "AI recommended action"),
                            detail=rec.get("detail", ""),
                            payload=rec.get("payload", {}),
                            snapshot=snap,
                        )
                        new_actions.append(action)
                    except ValueError as e:
                        logger.warning(f"[Assistant Proactive] Guardrail blocked LLM action: {e}")
        except Exception as e:
            logger.error(f"[Assistant Proactive] LLM failed: {e}")

    return [{"id": pa.id, "title": pa.title, "type": pa.action_type} for pa in new_actions]
