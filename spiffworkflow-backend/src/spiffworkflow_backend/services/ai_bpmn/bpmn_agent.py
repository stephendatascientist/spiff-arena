"""LangGraph agent that edits a BPMN diagram through compact operations instead of XML."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import TypedDict

from langchain_core.messages import AIMessage
from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from langgraph.graph import END
from langgraph.graph import START
from langgraph.graph import StateGraph
from pydantic import BaseModel
from pydantic import Field

from spiffworkflow_backend.services.ai_bpmn.bpmn_document import BpmnDocument
from spiffworkflow_backend.services.ai_bpmn.chat_utils import extract_json_object
from spiffworkflow_backend.services.ai_bpmn.chat_utils import history_messages
from spiffworkflow_backend.services.ai_bpmn.chat_utils import invoke_for_reply
from spiffworkflow_backend.services.ai_bpmn.chat_utils import strip_thinking
from spiffworkflow_backend.services.ai_bpmn.chat_utils import structured_model_for
from spiffworkflow_backend.services.ai_bpmn.chat_utils import usage_tokens
from spiffworkflow_backend.services.ai_bpmn.graph_ops import ProcessGraph
from spiffworkflow_backend.services.ai_bpmn.graph_ops import apply_ops
from spiffworkflow_backend.services.ai_bpmn.graph_ops import describe_changes
from spiffworkflow_backend.services.ai_bpmn.graph_ops import validate_graph
from spiffworkflow_backend.services.ai_bpmn.llm_provider import AiBpmnSettings
from spiffworkflow_backend.services.custom_parser import MyCustomParser

LOGGER = logging.getLogger(__name__)

SYSTEM_PROMPT = """You design BPMN process diagrams for SpiffWorkflow. You never write XML.
The current diagram is an outline, one item per line:
  <id> <type> "<name>"                          element
  <from> -> <to> ["<label>"] [if <condition>]   sequence flow
Types: start, end, task, user (person fills in a form), manual (person works outside the system), \
script (python code), service (external API call), call (runs another process), \
xor (exclusive gateway, one path), and (parallel gateway, all paths), or (inclusive gateway, one or more paths).
Elements with other types already exist in the diagram; you may connect or remove them but not add them.

Reply with JSON {"message": "<one or two sentences for the user>", "ops": ["<op>", ...]}.
Ops are applied in order to the current diagram:
  add <id> <type> "<name>"
  insert <id> <type> "<name>" between <from> <to>   (places an element on an existing flow)
  rename <id> "<name>"
  retype <id> <type>
  remove <id>                                       (a -> x -> b is reconnected to a -> b)
  connect <from> <to> ["<label>"] [if <python condition>]
  disconnect <from> <to>
Rules:
- Use existing ids exactly as given. New ids are short and unique, e.g. t1, g1, e2.
- To add a step on an existing path use insert ... between; connect alone keeps the old flow as well.
- Every element must be connected; every path starts at a start event and finishes at an end event.
- Name tasks verb + object ("Review request"); name xor gateways as a question ("Approved?").
- For an xor/or split, label each outgoing flow and give every flow except one a python condition on \
process variables (e.g. if approved == True). The flow without a condition is the default.
- When parallel or alternative paths continue together, merge them with a gateway of the same type.
- Only change what the user asks for. For a completely new process, remove the elements that no longer apply.
- If the request is not about the process diagram, return no ops and explain in message.
Example. Diagram: n1 start "Start". Request: "leave request approved by a manager".
{"message": "Added a leave request flow with manager approval.", "ops": ["add t1 user \\"Submit leave request\\"", \
"add t2 user \\"Review leave request\\"", "add g1 xor \\"Approved?\\"", "add t3 script \\"Record leave\\"", \
"add t4 manual \\"Inform employee of rejection\\"", "add e1 end \\"Leave recorded\\"", "add e2 end \\"Rejected\\"", \
"n1 -> t1", "t1 -> t2", "t2 -> g1", "g1 -> t3 \\"yes\\" if approved == True", "g1 -> t4 \\"no\\"", "t3 -> e1", "t4 -> e2"]}
Example edit. Diagram contains n6 -> n7. Request: "check the vendor before n7".
{"message": "Added a vendor check.", "ops": ["insert t1 user \\"Check vendor\\" between n6 n7"]}"""


class AssistantReply(BaseModel):
    message: str = Field(description="Short reply to the user describing what changed")
    ops: list[str] = Field(default_factory=list, description="Edit operations, one per item")


class AgentState(TypedDict, total=False):
    graph: ProcessGraph
    messages: list[BaseMessage]
    reply_message: str
    pending_ops: list[str]
    applied_ops: list[str]
    problems: list[str]
    attempts: int
    input_tokens: int
    output_tokens: int


@dataclass
class AiBpmnResult:
    bpmn_xml: str
    message: str
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ops: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bpmn_xml": self.bpmn_xml,
            "message": self.message,
            "changes": self.changes,
            "warnings": self.warnings,
            "ops": self.ops,
            "usage": self.usage,
        }


def parse_reply_text(text: str) -> AssistantReply:
    """Lenient parser for models that do not support structured output."""
    data = extract_json_object(text)
    if data is not None:
        ops = data.get("ops") or []
        if isinstance(ops, str):
            ops = ops.splitlines()
        return AssistantReply(message=str(data.get("message", "")), ops=[str(op) for op in ops])
    lines = [line for line in strip_thinking(text).strip("`").splitlines() if line.strip()]
    return AssistantReply(message="", ops=lines)


class AiBpmnAgent:
    def __init__(self, chat_model: Any, settings: AiBpmnSettings) -> None:
        self.chat_model = chat_model
        self.settings = settings
        self.structured_model = self._structured_model(chat_model)
        self.workflow = self._build_workflow()

    @staticmethod
    def _structured_model(chat_model: Any) -> Any | None:
        return structured_model_for(chat_model, AssistantReply)

    def _build_workflow(self) -> Any:
        workflow = StateGraph(AgentState)
        workflow.add_node("call_model", self._call_model)
        workflow.add_node("apply_and_validate", self._apply_and_validate)
        workflow.add_edge(START, "call_model")
        workflow.add_edge("call_model", "apply_and_validate")
        workflow.add_conditional_edges("apply_and_validate", self._needs_repair, {"repair": "call_model", "done": END})
        return workflow.compile()

    def _invoke_model(self, messages: list[BaseMessage]) -> tuple[AssistantReply, AIMessage | None]:
        return invoke_for_reply(self.chat_model, self.structured_model, AssistantReply, messages, parse_reply_text)

    def _call_model(self, state: AgentState) -> AgentState:
        messages = list(state["messages"])
        reply, raw = self._invoke_model(messages)
        messages.append(AIMessage(content=reply.model_dump_json()))
        input_tokens, output_tokens = usage_tokens(raw)
        return {
            "messages": messages,
            "reply_message": reply.message or state.get("reply_message", ""),
            "applied_ops": state.get("applied_ops", []),
            "attempts": state.get("attempts", 0) + 1,
            "input_tokens": state.get("input_tokens", 0) + input_tokens,
            "output_tokens": state.get("output_tokens", 0) + output_tokens,
            "problems": [],
            "graph": state["graph"],
            "pending_ops": reply.ops,
        }

    def _apply_and_validate(self, state: AgentState) -> AgentState:
        graph = state["graph"].copy()
        applied, op_errors = apply_ops(graph, state.get("pending_ops", []))
        problems = op_errors + validate_graph(graph) if (applied or op_errors) else []
        messages = list(state["messages"])
        if problems and state.get("attempts", 0) <= self.settings.max_repair_attempts:
            problem_lines = "\n".join(f"- {problem}" for problem in problems)
            messages.append(
                HumanMessage(
                    content=(
                        f"Your ops left these problems:\n{problem_lines}\n\nDiagram now:\n{graph.to_outline()}\n\n"
                        "Reply in the same JSON format with only the additional ops that fix the problems."
                    )
                )
            )
        return {
            "graph": graph,
            "messages": messages,
            "applied_ops": state.get("applied_ops", []) + applied,
            "problems": problems,
        }

    def _needs_repair(self, state: AgentState) -> str:
        if state.get("problems") and state.get("attempts", 0) <= self.settings.max_repair_attempts:
            return "repair"
        return "done"

    def _history_messages(self, history: list[dict[str, Any]]) -> list[BaseMessage]:
        return history_messages(history, self.settings.max_history_messages)

    def run(
        self,
        prompt: str,
        bpmn_xml: str | None = None,
        history: list[dict[str, Any]] | None = None,
        process_id: str | None = None,
    ) -> AiBpmnResult:
        document = BpmnDocument.from_xml(bpmn_xml, process_id)
        original_graph = document.graph.copy()
        messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
        messages += self._history_messages(history or [])
        messages.append(HumanMessage(content=f"Diagram:\n{document.outline()}\n\nRequest: {prompt.strip()}"))

        initial_state: AgentState = {"graph": document.graph.copy(), "messages": messages, "attempts": 0}
        final_state = self.workflow.invoke(initial_state, {"recursion_limit": 4 + 2 * (self.settings.max_repair_attempts + 1)})

        graph: ProcessGraph = final_state["graph"]
        warnings = [f"Unresolved: {problem}" for problem in final_state.get("problems", [])]
        changes = describe_changes(original_graph, graph)
        usage = {"input_tokens": final_state.get("input_tokens", 0), "output_tokens": final_state.get("output_tokens", 0)}
        message = final_state.get("reply_message") or ("Updated the diagram." if changes else "No changes were made.")
        if not changes:
            return AiBpmnResult(bpmn_xml=bpmn_xml or "", message=message, warnings=warnings, usage=usage)

        new_xml = document.apply_graph(graph)
        parse_warning = self._check_parses(new_xml, document.process_id)
        if parse_warning:
            warnings.append(parse_warning)
        return AiBpmnResult(
            bpmn_xml=new_xml,
            message=message,
            changes=changes,
            warnings=warnings,
            ops=final_state.get("applied_ops", []),
            usage=usage,
        )

    @staticmethod
    def _check_parses(bpmn_xml: str, process_id: str) -> str | None:
        try:
            parser = MyCustomParser()
            parser.add_bpmn_str(bpmn_xml.encode("utf-8"))
            parser.get_spec(process_id)
        except Exception as exception:
            if "Service Task must have an operator" in str(exception):
                return "Service tasks need a connector: select each service task and choose an operator in the properties panel."
            LOGGER.warning("AI BPMN result did not parse: %s", exception)
            return f"SpiffWorkflow could not parse the result: {exception}"
        return None
