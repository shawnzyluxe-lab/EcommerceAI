"""Vantav merchant assistant engine with memory, tools, context, and proactive actions."""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import agent_context
import agent_memory
import agent_tools
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
    context_str = agent_context.format_snapshot(snap)

    answer = (
        "Here's a quick read from your live data:\n\n" + context_str.replace("\n", "\n\n") +
        "\n\nAdd an OPENAI_API_KEY or DEEPSEEK_API_KEY environment variable to unlock natural-language reasoning and deeper analysis."
    )
    return {"answer": answer, "did": did}


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
