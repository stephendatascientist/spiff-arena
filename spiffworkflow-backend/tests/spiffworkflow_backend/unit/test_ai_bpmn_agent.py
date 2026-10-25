from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage

from spiffworkflow_backend.services.ai_bpmn.bpmn_agent import AiBpmnAgent
from spiffworkflow_backend.services.ai_bpmn.bpmn_agent import AssistantReply
from spiffworkflow_backend.services.ai_bpmn.bpmn_agent import parse_reply_text
from spiffworkflow_backend.services.ai_bpmn.bpmn_document import BpmnDocument
from spiffworkflow_backend.services.ai_bpmn.llm_provider import AiBpmnConfigurationError
from spiffworkflow_backend.services.ai_bpmn.llm_provider import AiBpmnSettings
from spiffworkflow_backend.services.ai_bpmn.llm_provider import create_chat_model


class ScriptedStructuredModel:
    def __init__(self, replies: list[AssistantReply], calls: list[list[BaseMessage]]) -> None:
        self.replies = replies
        self.calls = calls

    def invoke(self, messages: list[BaseMessage]) -> dict[str, Any]:
        self.calls.append(list(messages))
        reply = self.replies.pop(0)
        raw = AIMessage(
            content=reply.model_dump_json(), usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
        )
        return {"raw": raw, "parsed": reply, "parsing_error": None}


class ScriptedChatModel:
    """Minimal stand-in for a LangChain chat model."""

    def __init__(self, replies: list[AssistantReply], structured: bool = True, raw_texts: list[str] | None = None) -> None:
        self.replies = replies
        self.structured = structured
        self.raw_texts = raw_texts or []
        self.calls: list[list[BaseMessage]] = []

    def with_structured_output(self, schema: Any, include_raw: bool = False) -> ScriptedStructuredModel:
        if not self.structured:
            raise NotImplementedError
        return ScriptedStructuredModel(self.replies, self.calls)

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(list(messages))
        return AIMessage(content=self.raw_texts.pop(0))


def _agent(model: ScriptedChatModel, max_repair_attempts: int = 2) -> AiBpmnAgent:
    return AiBpmnAgent(model, AiBpmnSettings(max_repair_attempts=max_repair_attempts))


class TestAiBpmnAgent:
    def test_generates_a_diagram_from_a_prompt(self) -> None:
        model = ScriptedChatModel(
            [
                AssistantReply(
                    message="Added a review step.",
                    ops=["add n1 start", 'add t1 user "Review request"', 'add e1 end "Done"', "n1 -> t1", "t1 -> e1"],
                )
            ]
        )
        result = _agent(model).run("review a request")

        assert result.message == "Added a review step."
        assert result.warnings == []
        assert 'Added user task "Review request"' in result.changes
        assert result.usage == {"input_tokens": 100, "output_tokens": 20}
        assert len(model.calls) == 1
        prompt = model.calls[0][-1].content
        assert "(empty diagram)" in prompt
        assert "<bpmn" not in prompt
        assert 'user "Review request"' in BpmnDocument.from_xml(result.bpmn_xml).outline()

    def test_repairs_problems_with_another_model_call(self) -> None:
        model = ScriptedChatModel(
            [
                AssistantReply(message="Added a review step.", ops=["add n1 start", 'add t1 user "Review request"', "n1 -> t1"]),
                AssistantReply(message="", ops=['add e1 end "Done"', "t1 -> e1"]),
            ]
        )
        result = _agent(model).run("review a request")

        assert len(model.calls) == 2
        repair_prompt = model.calls[1][-1]
        assert isinstance(repair_prompt, HumanMessage)
        assert "t1 has no outgoing flow" in str(repair_prompt.content)
        assert result.warnings == []
        assert result.message == "Added a review step."
        assert result.usage["input_tokens"] == 200
        assert result.ops == ["add n1 start", 'add t1 user "Review request"', "n1 -> t1", 'add e1 end "Done"', "t1 -> e1"]

    def test_reports_unresolved_problems_after_max_attempts(self) -> None:
        model = ScriptedChatModel(
            [AssistantReply(message="Added a task.", ops=["add n1 start", 'add t1 task "Do work"', "n1 -> t1"])]
        )
        result = _agent(model, max_repair_attempts=0).run("do work")

        assert len(model.calls) == 1
        assert any("t1 has no outgoing flow" in warning for warning in result.warnings)
        assert result.changes

    def test_returns_original_xml_when_nothing_changes(self) -> None:
        original = BpmnDocument.from_xml(None).to_xml()
        model = ScriptedChatModel([AssistantReply(message="I can only help with process diagrams.", ops=[])])
        result = _agent(model).run("what is the weather?", original)

        assert result.bpmn_xml == original
        assert result.changes == []
        assert result.message == "I can only help with process diagrams."

    def test_falls_back_to_text_parsing_without_structured_output(self) -> None:
        text = '<think>hmm</think>```json\n{"message": "Done.", "ops": ["add n1 start", "add e1 end", "n1 -> e1"]}\n```'
        model = ScriptedChatModel([], structured=False, raw_texts=[text])
        result = _agent(model).run("simplest process")

        assert result.message == "Done."
        assert result.warnings == []
        assert any("end event" in change for change in result.changes)

    def test_history_is_limited_and_passed_to_the_model(self) -> None:
        model = ScriptedChatModel([AssistantReply(message="ok", ops=[])])
        history = [{"role": "user", "content": f"message {index}"} for index in range(10)]
        AiBpmnAgent(model, AiBpmnSettings(max_history_messages=2)).run("next", None, history)

        contents = [str(message.content) for message in model.calls[0]]
        assert "message 9" in contents
        assert "message 8" in contents
        assert "message 7" not in contents


class TestParseReplyText:
    def test_plain_lines_become_ops(self) -> None:
        reply = parse_reply_text("add t1 task\nn1 -> t1\n")
        assert reply.ops == ["add t1 task", "n1 -> t1"]

    def test_ops_can_be_a_single_string(self) -> None:
        reply = parse_reply_text('{"message": "hi", "ops": "add t1 task\\nn1 -> t1"}')
        assert reply.message == "hi"
        assert reply.ops == ["add t1 task", "n1 -> t1"]


class TestLlmProvider:
    def test_creates_an_ollama_model_by_default(self) -> None:
        model = create_chat_model(AiBpmnSettings(model="llama3.2", base_url="http://ollama:11434", timeout_seconds=7))
        assert type(model).__name__ == "ChatOllama"
        assert model.model == "llama3.2"
        assert model.base_url == "http://ollama:11434"
        assert model.reasoning is False

    def test_model_kwargs_override_defaults(self) -> None:
        model = create_chat_model(AiBpmnSettings(model_kwargs={"reasoning": True, "num_ctx": 8192}))
        assert model.reasoning is True
        assert model.num_ctx == 8192

    def test_unknown_provider(self) -> None:
        with pytest.raises(AiBpmnConfigurationError, match="Unknown AI BPMN provider"):
            create_chat_model(AiBpmnSettings(provider="nope"))

    def test_missing_provider_package_gives_install_hint(self) -> None:
        with pytest.raises(AiBpmnConfigurationError, match="pip install"):
            create_chat_model(AiBpmnSettings(provider="claude", api_key="x"))

    def test_custom_class_path(self) -> None:
        model = create_chat_model(AiBpmnSettings(provider="langchain_ollama:ChatOllama", model="custom"))
        assert model.model == "custom"
