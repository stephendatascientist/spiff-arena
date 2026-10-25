"""Helpers shared by the LangGraph assistants."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from typing import TypeVar

from langchain_core.messages import AIMessage
from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

ReplyT = TypeVar("ReplyT", bound=BaseModel)


def strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def extract_json_object(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", strip_thinking(text), flags=re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def structured_model_for(chat_model: Any, schema: type[BaseModel]) -> Any | None:
    try:
        return chat_model.with_structured_output(schema, include_raw=True)
    except (NotImplementedError, AttributeError, TypeError):
        return None


def _content_text(raw: Any) -> str:
    content = getattr(raw, "content", "") if raw is not None else ""
    return content if isinstance(content, str) else json.dumps(content)


def invoke_for_reply(
    chat_model: Any,
    structured_model: Any | None,
    schema: type[ReplyT],
    messages: list[BaseMessage],
    parse_text: Callable[[str], ReplyT],
) -> tuple[ReplyT, AIMessage | None]:
    """Prefer structured output; fall back to lenient text parsing for models without tool calling."""
    if structured_model is not None:
        result = structured_model.invoke(messages)
        raw = result.get("raw") if isinstance(result, dict) else None
        parsed = result.get("parsed") if isinstance(result, dict) else result
        if isinstance(parsed, schema):
            return parsed, raw
        if isinstance(parsed, dict):
            return schema.model_validate(parsed), raw
        return parse_text(_content_text(raw)), raw
    raw = chat_model.invoke(messages)
    return parse_text(_content_text(raw)), raw


def usage_tokens(raw: Any) -> tuple[int, int]:
    usage = getattr(raw, "usage_metadata", None) or {}
    return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)


def history_messages(history: list[dict[str, Any]], limit: int) -> list[BaseMessage]:
    recent = history[-limit:] if limit > 0 else []
    messages: list[BaseMessage] = []
    for item in recent:
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        if item.get("role") == "assistant":
            messages.append(AIMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))
    return messages
