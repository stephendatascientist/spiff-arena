"""LangGraph agent that writes or edits script task code and user-task instructions (markdown + Jinja)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Literal
from typing import TypedDict

import jinja2
from jinja2 import meta as jinja_meta
from langchain_core.messages import AIMessage
from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from langgraph.graph import END
from langgraph.graph import START
from langgraph.graph import StateGraph
from pydantic import BaseModel
from pydantic import Field
from RestrictedPython import compile_restricted_exec

from spiffworkflow_backend.services.ai_bpmn.chat_utils import extract_json_object
from spiffworkflow_backend.services.ai_bpmn.chat_utils import history_messages
from spiffworkflow_backend.services.ai_bpmn.chat_utils import invoke_for_reply
from spiffworkflow_backend.services.ai_bpmn.chat_utils import strip_thinking
from spiffworkflow_backend.services.ai_bpmn.chat_utils import structured_model_for
from spiffworkflow_backend.services.ai_bpmn.chat_utils import usage_tokens
from spiffworkflow_backend.services.ai_bpmn.llm_provider import AiBpmnSettings

LOGGER = logging.getLogger(__name__)

TextKind = Literal["script", "markdown"]
# (script, input context, expected output context) -> (passed, problem description)
UnitTestRunner = Callable[[str, dict[str, Any], dict[str, Any]], tuple[bool, str | None]]

MAX_UNIT_TESTS = 5
MAX_JSON_CHARS = 1000
MAX_CURRENT_CONTENT_CHARS = 12000

SCRIPT_SYSTEM_PROMPT = """You write Python for SpiffWorkflow script tasks.
Rules:
- The script runs in a restricted Python sandbox. Task data variables are plain global variables: read them directly.
  Assign new variables to produce output; every variable left at the end becomes task data.
- Never use import. Already available: {modules}. Builtins: {builtins}. Spiff functions are listed in the request.
- No print, no classes, no file or network access, no names starting with an underscore.
- Keep it short and readable. Add comments only if the user asks.
- When changing an existing script, return the complete updated script.
Reply only as JSON: {{"message": "<one short sentence about what you did>", "content": "<the complete script>"}}"""

MARKDOWN_SYSTEM_PROMPT = """You write the instructions shown to end users on SpiffWorkflow tasks, as GitHub-flavored Markdown.
Rules:
- Show task data with Jinja2: {{{{ variable }}}}, {{% if %}}, {{% for item in items %}}.
  Prefer the known variables; do not invent data unless the user names it.
- Extra Jinja filters and functions available: {helpers}.
- No raw HTML unless asked. Keep it concise, friendly and well structured (headings, lists, tables when useful).
- When changing existing text, return the complete updated text.
Reply only as JSON: {{"message": "<one short sentence about what you did>", "content": "<the complete markdown>"}}"""

FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


class TextAssistReply(BaseModel):
    message: str = Field(description="One short sentence describing what was written or changed")
    content: str = Field(description="The complete script or markdown")


def strip_fences(text: str) -> str:
    match = FENCE_RE.match(text)
    return match.group(1) if match else text


def parse_text_reply(text: str) -> TextAssistReply:
    data = extract_json_object(text)
    if data is not None and "content" in data:
        return TextAssistReply(message=str(data.get("message", "")), content=str(data.get("content") or ""))
    return TextAssistReply(message="", content=strip_fences(strip_thinking(text)))


@dataclass
class TextAssistRequest:
    kind: TextKind
    prompt: str
    current_content: str = ""
    element_name: str | None = None
    element_type: str | None = None
    script_type: str | None = None
    unit_tests: list[dict[str, Any]] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    spiff_functions: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    builtins: list[str] = field(default_factory=list)
    jinja_helpers: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    restricted: bool = True


@dataclass
class TextAssistResult:
    content: str
    message: str
    warnings: list[str] = field(default_factory=list)
    tests: dict[str, int] | None = None
    usage: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            # kept for clients of the original OpenAI based script assist endpoint
            "result": self.content,
            "message": self.message,
            "warnings": self.warnings,
            "tests": self.tests,
            "usage": self.usage,
        }


class TextAssistState(TypedDict, total=False):
    messages: list[BaseMessage]
    content: str
    reply_message: str
    problems: list[str]
    warnings: list[str]
    tests: dict[str, int] | None
    attempts: int
    input_tokens: int
    output_tokens: int


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _as_context(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value or "{}")
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


class TextAssistAgent:
    def __init__(self, chat_model: Any, settings: AiBpmnSettings, unit_test_runner: UnitTestRunner | None = None) -> None:
        self.chat_model = chat_model
        self.settings = settings
        self.unit_test_runner = unit_test_runner
        self.structured_model = structured_model_for(chat_model, TextAssistReply)

    def _build_workflow(self, request: TextAssistRequest) -> Any:
        workflow = StateGraph(TextAssistState)
        workflow.add_node("call_model", self._call_model)
        workflow.add_node("validate", lambda state: self._validate(state, request))
        workflow.add_edge(START, "call_model")
        workflow.add_edge("call_model", "validate")
        workflow.add_conditional_edges("validate", self._needs_repair, {"repair": "call_model", "done": END})
        return workflow.compile()

    def _call_model(self, state: TextAssistState) -> TextAssistState:
        messages = list(state["messages"])
        reply, raw = invoke_for_reply(self.chat_model, self.structured_model, TextAssistReply, messages, parse_text_reply)
        messages.append(AIMessage(content=reply.model_dump_json()))
        input_tokens, output_tokens = usage_tokens(raw)
        return {
            "messages": messages,
            "content": strip_fences(reply.content).strip("\n"),
            "reply_message": reply.message or state.get("reply_message", ""),
            "attempts": state.get("attempts", 0) + 1,
            "input_tokens": state.get("input_tokens", 0) + input_tokens,
            "output_tokens": state.get("output_tokens", 0) + output_tokens,
            "problems": [],
        }

    def _validate(self, state: TextAssistState, request: TextAssistRequest) -> TextAssistState:
        content = state.get("content", "")
        if request.kind == "script":
            problems, warnings, tests = self._validate_script(content, request)
        else:
            problems, warnings, tests = self._validate_markdown(content, request)
        if not content.strip():
            problems = ["The content is empty."]
        messages = list(state["messages"])
        if problems and state.get("attempts", 0) <= self.settings.max_repair_attempts:
            problem_lines = "\n".join(f"- {problem}" for problem in problems)
            messages.append(
                HumanMessage(
                    content=(
                        f"That has problems:\n{problem_lines}\n\n"
                        "Reply in the same JSON format with the complete corrected content."
                    )
                )
            )
        return {"messages": messages, "problems": problems, "warnings": warnings, "tests": tests}

    def _needs_repair(self, state: TextAssistState) -> str:
        if state.get("problems") and state.get("attempts", 0) <= self.settings.max_repair_attempts:
            return "repair"
        return "done"

    @staticmethod
    def check_script_syntax(script: str, restricted: bool = True) -> list[str]:
        if restricted:
            result = compile_restricted_exec(script)
            return [str(error) for error in result.errors]
        try:
            compile(script, "<script>", "exec")
        except SyntaxError as exception:
            return [f"Line {exception.lineno}: SyntaxError: {exception.msg}"]
        return []

    def _validate_script(self, content: str, request: TextAssistRequest) -> tuple[list[str], list[str], dict[str, int] | None]:
        problems = self.check_script_syntax(content, request.restricted)
        if problems or not request.unit_tests or self.unit_test_runner is None:
            return problems, [], None
        passed = 0
        warnings: list[str] = []
        tests = request.unit_tests[:MAX_UNIT_TESTS]
        for index, unit_test in enumerate(tests, start=1):
            input_context = _as_context(unit_test.get("input"))
            expected = _as_context(unit_test.get("expected"))
            if input_context is None or expected is None:
                warnings.append(f"Unit test {index} has invalid JSON and was skipped.")
                continue
            ok, problem = self.unit_test_runner(content, input_context, expected)
            if ok:
                passed += 1
            else:
                problems.append(f"Unit test {index} failed: {_truncate(problem or 'unexpected result', 600)}")
        return problems, warnings, {"passed": passed, "total": len(tests)}

    @staticmethod
    def _validate_markdown(content: str, request: TextAssistRequest) -> tuple[list[str], list[str], dict[str, int] | None]:
        environment = jinja2.Environment(autoescape=True)
        try:
            parsed = environment.parse(content)
        except jinja2.TemplateSyntaxError as exception:
            return [f"Jinja syntax error on line {exception.lineno}: {exception.message}"], [], None
        warnings: list[str] = []
        if request.variables:
            known = {name.split(".")[0] for name in request.variables} | set(request.jinja_helpers)
            unknown = sorted(jinja_meta.find_undeclared_variables(parsed) - known)  # type: ignore[no-untyped-call]
            if unknown:
                warnings.append(
                    "These variables are not set anywhere else in the process; make sure they exist in task data: "
                    + ", ".join(unknown)
                )
        return [], warnings, None

    def _system_prompt(self, request: TextAssistRequest) -> str:
        if request.kind == "script":
            return SCRIPT_SYSTEM_PROMPT.format(modules=", ".join(request.modules), builtins=", ".join(request.builtins))
        return MARKDOWN_SYSTEM_PROMPT.format(helpers=", ".join(request.jinja_helpers) or "none")

    @staticmethod
    def _request_message(request: TextAssistRequest) -> str:
        parts: list[str] = []
        if request.element_name or request.element_type:
            where = f'"{request.element_name}"' if request.element_name else "unnamed"
            kind = request.element_type or "task"
            if request.kind == "script":
                timing = {"preScript": "runs before the task", "postScript": "runs after the task"}.get(
                    request.script_type or "", "is the script of a script task"
                )
                parts.append(f"Task: {where} ({kind}); this script {timing}.")
            else:
                parts.append(f"Task: {where} ({kind}); this text is shown to the person completing it.")
        if request.variables:
            parts.append("Known task data variables: " + ", ".join(request.variables))
        if request.kind == "script" and request.spiff_functions:
            parts.append("Spiff functions:\n" + "\n".join(f"- {line}" for line in request.spiff_functions))
        if request.kind == "script" and request.unit_tests:
            lines = []
            for index, unit_test in enumerate(request.unit_tests[:MAX_UNIT_TESTS], start=1):
                given = _truncate(json.dumps(_as_context(unit_test.get("input")), separators=(",", ":")), MAX_JSON_CHARS)
                wanted = _truncate(json.dumps(_as_context(unit_test.get("expected")), separators=(",", ":")), MAX_JSON_CHARS)
                lines.append(f"{index}. input {given} -> expected task data {wanted}")
            parts.append("Unit tests the script must pass:\n" + "\n".join(lines))
        label = "script" if request.kind == "script" else "instructions"
        if request.current_content.strip():
            parts.append(f"Current {label}:\n```\n{_truncate(request.current_content, MAX_CURRENT_CONTENT_CHARS)}\n```")
        else:
            parts.append(f"Current {label}: (empty)")
        parts.append(f"Request: {request.prompt.strip()}")
        return "\n\n".join(parts)

    def run(self, request: TextAssistRequest) -> TextAssistResult:
        messages: list[BaseMessage] = [SystemMessage(content=self._system_prompt(request))]
        messages += history_messages(request.history, self.settings.max_history_messages)
        messages.append(HumanMessage(content=self._request_message(request)))

        workflow = self._build_workflow(request)
        initial_state: TextAssistState = {"messages": messages, "attempts": 0}
        final_state = workflow.invoke(initial_state, {"recursion_limit": 4 + 2 * (self.settings.max_repair_attempts + 1)})

        content = final_state.get("content", "")
        warnings = list(final_state.get("warnings", []))
        warnings += [f"Unresolved: {problem}" for problem in final_state.get("problems", [])]
        usage = {"input_tokens": final_state.get("input_tokens", 0), "output_tokens": final_state.get("output_tokens", 0)}
        default_message = "Here is the updated script." if request.kind == "script" else "Here are the updated instructions."
        return TextAssistResult(
            content=content,
            message=final_state.get("reply_message") or default_message,
            warnings=warnings,
            tests=final_state.get("tests"),
            usage=usage,
        )
