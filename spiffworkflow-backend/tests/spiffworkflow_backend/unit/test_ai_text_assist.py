import json
from typing import Any

from flask.app import Flask
from langchain_core.messages import AIMessage
from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage

from spiffworkflow_backend.services.ai_bpmn import text_assist_context
from spiffworkflow_backend.services.ai_bpmn.llm_provider import AiBpmnSettings
from spiffworkflow_backend.services.ai_bpmn.text_assist_agent import TextAssistAgent
from spiffworkflow_backend.services.ai_bpmn.text_assist_agent import TextAssistReply
from spiffworkflow_backend.services.ai_bpmn.text_assist_agent import TextAssistRequest
from spiffworkflow_backend.services.ai_bpmn.text_assist_agent import parse_text_reply
from spiffworkflow_backend.services.ai_bpmn.text_assist_agent import strip_fences
from spiffworkflow_backend.services.file_system_service import FileSystemService
from tests.spiffworkflow_backend.helpers.base_test import BaseTest


class ScriptedStructuredModel:
    def __init__(self, replies: list[TextAssistReply], calls: list[list[BaseMessage]]) -> None:
        self.replies = replies
        self.calls = calls

    def invoke(self, messages: list[BaseMessage]) -> dict[str, Any]:
        self.calls.append(list(messages))
        reply = self.replies.pop(0)
        raw = AIMessage(
            content=reply.model_dump_json(), usage_metadata={"input_tokens": 50, "output_tokens": 10, "total_tokens": 60}
        )
        return {"raw": raw, "parsed": reply, "parsing_error": None}


class ScriptedChatModel:
    def __init__(self, replies: list[TextAssistReply], structured: bool = True, raw_texts: list[str] | None = None) -> None:
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


def _script_runner(script: str, input_context: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, str | None]:
    context = dict(input_context)
    exec(script, {}, context)  # noqa: S102
    if context == expected:
        return True, None
    return False, f"expected {expected} but got {context}"


SCHEMA_PROPERTIES = {
    "type": "object",
    "properties": {"first_name": {"type": "string"}, "address": {"type": "object", "properties": {"city": {"type": "string"}}}},
}

DIAGRAM = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
    xmlns:spiffworkflow="http://spiffworkflow.org/bpmn/schema/1.0/core" id="d" targetNamespace="t">
  <bpmn:process id="p" isExecutable="true">
    <bpmn:scriptTask id="calc" name="Calculate">
      <bpmn:script>total = sum([p * quantity for p in prices]) + len(json.dumps(x))</bpmn:script>
    </bpmn:scriptTask>
    <bpmn:scriptTask id="current" name="Current">
      <bpmn:script>secret_local = 1</bpmn:script>
    </bpmn:scriptTask>
    <bpmn:userTask id="review" name="Review">
      <bpmn:extensionElements>
<spiffworkflow:instructionsForEndUser>{{ customer_name }}{% if approved %}ok{% endif %}</spiffworkflow:instructionsForEndUser>
        <spiffworkflow:preScript>discount = 0.1</spiffworkflow:preScript>
        <spiffworkflow:properties>
          <spiffworkflow:property name="formJsonSchemaFilename" value="review-schema.json" />
        </spiffworkflow:properties>
      </bpmn:extensionElements>
    </bpmn:userTask>
    <bpmn:serviceTask id="svc">
      <bpmn:extensionElements>
        <spiffworkflow:serviceTaskOperator id="http/GetRequest" resultVariable="api_response" />
      </bpmn:extensionElements>
    </bpmn:serviceTask>
    <bpmn:sequenceFlow id="f1" sourceRef="calc" targetRef="review">
      <bpmn:conditionExpression>total &gt; limit</bpmn:conditionExpression>
    </bpmn:sequenceFlow>
  </bpmn:process>
</bpmn:definitions>"""


class TestTextAssistAgent:
    def test_writes_a_script_and_reports_usage(self) -> None:
        model = ScriptedChatModel([TextAssistReply(message="Added a sum.", content="```python\ntotal = a + b\n```")])
        agent = TextAssistAgent(model, AiBpmnSettings())
        result = agent.run(
            TextAssistRequest(kind="script", prompt="add a and b", modules=["json"], builtins=["sum"], variables=["a", "b"])
        )
        assert result.content == "total = a + b"
        assert result.message == "Added a sum."
        assert result.warnings == []
        assert result.tests is None
        assert result.usage == {"input_tokens": 50, "output_tokens": 10}
        assert result.to_dict()["result"] == "total = a + b"
        prompt = model.calls[0][-1].content
        assert "Known task data variables: a, b" in prompt
        assert "Current script: (empty)" in prompt
        assert "Never use import. Already available: json" in model.calls[0][0].content

    def test_repairs_a_script_that_does_not_compile_in_the_sandbox(self) -> None:
        model = ScriptedChatModel(
            [
                TextAssistReply(message="v1", content="import os\n_hidden = 1"),
                TextAssistReply(message="v2", content="hidden = 1"),
            ]
        )
        result = TextAssistAgent(model, AiBpmnSettings()).run(TextAssistRequest(kind="script", prompt="set hidden"))
        assert result.content == "hidden = 1"
        assert result.warnings == []
        assert len(model.calls) == 2
        repair = model.calls[1][-1]
        assert isinstance(repair, HumanMessage)
        assert "_hidden" in str(repair.content)

    def test_repairs_a_script_until_its_unit_tests_pass(self) -> None:
        model = ScriptedChatModel(
            [
                TextAssistReply(message="v1", content="total = a - b"),
                TextAssistReply(message="v2", content="total = a + b"),
            ]
        )
        agent = TextAssistAgent(model, AiBpmnSettings(), _script_runner)
        request = TextAssistRequest(
            kind="script",
            prompt="total of a and b",
            current_content="total = 0",
            unit_tests=[
                {"input": {"a": 1, "b": 2}, "expected": {"a": 1, "b": 2, "total": 3}},
                {"input": '{"a": 5, "b": 5}', "expected": '{"a": 5, "b": 5, "total": 10}'},
            ],
        )
        result = agent.run(request)
        assert result.content == "total = a + b"
        assert result.tests == {"passed": 2, "total": 2}
        assert result.warnings == []
        first_prompt = str(model.calls[0][-1].content)
        assert 'input {"a":1,"b":2} -> expected task data {"a":1,"b":2,"total":3}' in first_prompt
        assert "Current script:\n```\ntotal = 0\n```" in first_prompt
        assert "Unit test 1 failed" in str(model.calls[1][-1].content)

    def test_reports_unresolved_problems_after_the_repair_limit(self) -> None:
        model = ScriptedChatModel([TextAssistReply(message="bad", content="total = a - b")])
        agent = TextAssistAgent(model, AiBpmnSettings(max_repair_attempts=0), _script_runner)
        result = agent.run(
            TextAssistRequest(kind="script", prompt="sum", unit_tests=[{"input": {"a": 1, "b": 1}, "expected": {"total": 2}}])
        )
        assert result.tests == {"passed": 0, "total": 1}
        assert result.warnings[0].startswith("Unresolved: Unit test 1 failed")

    def test_writes_markdown_and_warns_about_unknown_variables(self) -> None:
        model = ScriptedChatModel(
            [
                TextAssistReply(message="bad", content="Hi {{ name "),
                TextAssistReply(message="Greeting added.", content="## Hi {{ first_name }}\nOrder {{ order_id | default('') }}"),
            ]
        )
        result = TextAssistAgent(model, AiBpmnSettings()).run(
            TextAssistRequest(
                kind="markdown",
                prompt="greet the user",
                variables=["first_name", "address.city"],
                jinja_helpers=["sanitize_for_md"],
                element_name="Review",
                element_type="bpmn:UserTask",
            )
        )
        assert result.content.startswith("## Hi {{ first_name }}")
        assert result.message == "Greeting added."
        assert result.warnings == [
            "These variables are not set anywhere else in the process; make sure they exist in task data: order_id"
        ]
        assert "Jinja syntax error" in str(model.calls[1][-1].content)
        assert "sanitize_for_md" in str(model.calls[0][0].content)
        assert "{{ variable }}" in str(model.calls[0][0].content)
        assert 'Task: "Review" (bpmn:UserTask)' in str(model.calls[0][-1].content)

    def test_falls_back_to_plain_text_replies_and_keeps_history(self) -> None:
        model = ScriptedChatModel(
            [],
            structured=False,
            raw_texts=['<think>hmm</think>{"message": "Done.", "content": "x = 1"}'],
        )
        result = TextAssistAgent(model, AiBpmnSettings(max_history_messages=2)).run(
            TextAssistRequest(
                kind="script",
                prompt="set x",
                history=[
                    {"role": "user", "content": "old"},
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "ok"},
                ],
            )
        )
        assert result.content == "x = 1"
        assert result.message == "Done."
        contents = [message.content for message in model.calls[0]]
        assert "old" not in contents
        assert "first" in contents and "ok" in contents

    def test_parse_helpers(self) -> None:
        assert strip_fences("```markdown\n# Title\n```") == "# Title"
        assert strip_fences("x = 1") == "x = 1"
        assert parse_text_reply("```python\ny = 2\n```").content == "y = 2"

    def test_syntax_check_without_restricted_engine(self) -> None:
        assert TextAssistAgent.check_script_syntax("_private = 1", restricted=False) == []
        assert TextAssistAgent.check_script_syntax("_private = 1", restricted=True) != []
        assert TextAssistAgent.check_script_syntax("x = (", restricted=False)[0].startswith("Line 1: SyntaxError")


class TestTextAssistContext(BaseTest):
    def test_process_variables_from_diagram(self) -> None:
        names = text_assist_context.process_variables(None, DIAGRAM, exclude_element_id="current")
        assert "secret_local" not in names
        for ignored in ["p", "sum", "len", "json"]:
            assert ignored not in names
        for expected in ["total", "customer_name", "approved", "discount", "api_response", "limit", "prices", "quantity", "x"]:
            assert expected in names
        assert text_assist_context.process_variables(None, "<broken") == []

    def test_process_variables_from_form_schemas(self, app: Flask, with_db_and_bpmn_file_cleanup: None) -> None:
        self.create_process_group("ai-group")
        process_model = self.create_process_model("ai-group/ai-model")
        FileSystemService.write_file_data_to_system(
            FileSystemService.full_file_path(process_model, "review-schema.json"), json.dumps(SCHEMA_PROPERTIES).encode()
        )
        names = text_assist_context.process_variables("ai-group:ai-model", None)
        assert names == ["first_name", "address", "address.city"]
        assert text_assist_context.process_variables("ai-group:missing", None) == []

    def test_spiff_functions_and_helpers(self, app: Flask) -> None:
        functions = text_assist_context.spiff_functions()
        assert "get_current_user(): Return the current user." in functions
        assert all(len(line) < 140 for line in functions)
        assert "sanitize_for_md" in text_assist_context.jinja_helpers()
