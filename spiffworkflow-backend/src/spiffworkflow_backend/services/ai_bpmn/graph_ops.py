"""In-memory process graph, compact outline format and the edit-operation DSL used with the LLM.

Outline (what the model sees), one element per line:

    n1 start "Start"
    n2 user "Review request"
    n3 xor "Approved?"
    n1 -> n2
    n3 -> n4 "yes" if approved == True

Operations (what the model returns), one per line:

    add <id> <type> "<name>"
    insert <id> <type> "<name>" between <from> <to>
    rename <id> "<name>"
    retype <id> <type>
    remove <id>
    connect <from> <to> ["<label>"] [if <python condition>]
    disconnect <from> <to>
"""

from __future__ import annotations

import copy
import re
from collections import deque
from dataclasses import dataclass
from dataclasses import field

# short keyword used with the LLM -> BPMN element tag
NODE_TYPES: dict[str, str] = {
    "start": "startEvent",
    "end": "endEvent",
    "task": "task",
    "user": "userTask",
    "manual": "manualTask",
    "script": "scriptTask",
    "service": "serviceTask",
    "call": "callActivity",
    "xor": "exclusiveGateway",
    "and": "parallelGateway",
    "or": "inclusiveGateway",
}
TAG_TO_TYPE: dict[str, str] = {tag: type_key for type_key, tag in NODE_TYPES.items()}
GATEWAY_TYPES = {"xor", "and", "or"}
CONDITIONAL_GATEWAY_TYPES = {"xor", "or"}

TYPE_LABELS: dict[str, str] = {
    "start": "start event",
    "end": "end event",
    "task": "task",
    "user": "user task",
    "manual": "manual task",
    "script": "script task",
    "service": "service task",
    "call": "call activity",
    "xor": "exclusive gateway",
    "and": "parallel gateway",
    "or": "inclusive gateway",
}

# lenient aliases so small models that use BPMN names still work
TYPE_ALIASES: dict[str, str] = {
    **{type_key: type_key for type_key in NODE_TYPES},
    **{tag.lower(): type_key for tag, type_key in TAG_TO_TYPE.items()},
    "startevent": "start",
    "endevent": "end",
    "usertask": "user",
    "human": "user",
    "form": "user",
    "manualtask": "manual",
    "scripttask": "script",
    "servicetask": "service",
    "callactivity": "call",
    "subprocess": "call",
    "exclusive": "xor",
    "decision": "xor",
    "gateway": "xor",
    "parallel": "and",
    "inclusive": "or",
    "activity": "task",
}

_ID = r"([A-Za-z_][\w.-]*)"
_QUOTED = r"(?:\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)')"
_CONDITION = r"(?:\s+(?:if|when)\s+(.+?))?"
_OP_PATTERNS: dict[str, re.Pattern[str]] = {
    "add": re.compile(rf"^(?:add\s+)?{_ID}\s+([A-Za-z]+)(?:\s+{_QUOTED})?$", re.IGNORECASE),
    "insert": re.compile(
        rf"^insert\s+{_ID}\s+([A-Za-z]+)(?:\s+{_QUOTED})?\s+between\s+{_ID}\s+(?:and\s+)?{_ID}$",
        re.IGNORECASE,
    ),
    "rename": re.compile(rf"^rename\s+{_ID}\s+{_QUOTED}$", re.IGNORECASE),
    "retype": re.compile(rf"^retype\s+{_ID}\s+([A-Za-z]+)$", re.IGNORECASE),
    "remove": re.compile(rf"^(?:remove|delete)\s+{_ID}$", re.IGNORECASE),
    "connect": re.compile(rf"^(?:connect\s+)?{_ID}\s*(?:->|\s)\s*{_ID}(?:\s+{_QUOTED})?{_CONDITION}$", re.IGNORECASE),
    "disconnect": re.compile(rf"^disconnect\s+{_ID}\s*(?:->|\s)\s*{_ID}$", re.IGNORECASE),
}
_OP_KEYWORDS = {"add", "insert", "rename", "retype", "remove", "delete", "connect", "disconnect"}


class GraphOpError(Exception):
    pass


@dataclass
class Node:
    id: str
    # keyword from NODE_TYPES, or the raw BPMN tag for elements the assistant does not edit
    type: str
    name: str = ""
    bpmn_id: str | None = None
    attached_to: str | None = None

    @property
    def supported(self) -> bool:
        return self.type in NODE_TYPES

    def label(self) -> str:
        type_label = TYPE_LABELS.get(self.type, self.type)
        return f'{type_label} "{self.name}"' if self.name else f"{type_label} {self.id}"


@dataclass
class Edge:
    source: str
    target: str
    label: str = ""
    condition: str = ""
    bpmn_id: str | None = None


@dataclass
class ProcessGraph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    def copy(self) -> ProcessGraph:
        return copy.deepcopy(self)

    def find_edge(self, source: str, target: str) -> Edge | None:
        for edge in self.edges:
            if edge.source == source and edge.target == target:
                return edge
        return None

    def incoming(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.target == node_id]

    def outgoing(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.source == node_id]

    def next_handle(self) -> str:
        index = len(self.nodes) + 1
        while f"n{index}" in self.nodes:
            index += 1
        return f"n{index}"

    def to_outline(self) -> str:
        if not self.nodes:
            return "(empty diagram)"
        lines = []
        for node in self.nodes.values():
            line = f"{node.id} {node.type}"
            if node.name:
                line += f' "{_quote(node.name)}"'
            if node.attached_to:
                line += f" @{node.attached_to}"
            lines.append(line)
        for edge in self.edges:
            line = f"{edge.source} -> {edge.target}"
            if edge.label:
                line += f' "{_quote(edge.label)}"'
            if edge.condition:
                line += f" if {edge.condition}"
            lines.append(line)
        return "\n".join(lines)


def _quote(value: str) -> str:
    return " ".join(value.replace('"', "'").split())


def _unquote(double_quoted: str | None, single_quoted: str | None) -> str | None:
    value = double_quoted if double_quoted is not None else single_quoted
    if value is None:
        return None
    return re.sub(r"\\(.)", r"\1", value).strip()


def normalize_type(raw_type: str) -> str:
    type_key = TYPE_ALIASES.get(raw_type.lower())
    if type_key is None:
        raise GraphOpError(f"unknown type '{raw_type}', use one of: {', '.join(NODE_TYPES)}")
    return type_key


def _clean_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^(?:[-*]\s+|\d+[.)]\s+)", "", line)
    return line.strip().strip("`").strip().rstrip(";").strip()


def apply_op(graph: ProcessGraph, raw_line: str) -> None:
    line = _clean_line(raw_line)
    if not line or line.startswith("#"):
        return
    first_word = line.split()[0].lower()

    if first_word in ("insert",):
        match = _OP_PATTERNS["insert"].match(line)
        if match:
            node_id, raw_type, dq, sq, source, target = match.groups()
            _insert(graph, node_id, normalize_type(raw_type), _unquote(dq, sq) or "", source, target)
            return
    elif first_word == "rename":
        match = _OP_PATTERNS["rename"].match(line)
        if match:
            node_id, dq, sq = match.groups()
            _require_node(graph, node_id).name = _unquote(dq, sq) or ""
            return
    elif first_word == "retype":
        match = _OP_PATTERNS["retype"].match(line)
        if match:
            node_id, raw_type = match.groups()
            node = _require_node(graph, node_id)
            if not node.supported:
                raise GraphOpError(f"{node_id} is a {node.type} which cannot be changed by the assistant")
            node.type = normalize_type(raw_type)
            return
    elif first_word in ("remove", "delete"):
        match = _OP_PATTERNS["remove"].match(line)
        if match:
            _remove(graph, match.group(1))
            return
    elif first_word == "disconnect":
        match = _OP_PATTERNS["disconnect"].match(line)
        if match:
            source, target = match.groups()
            edge = graph.find_edge(source, target)
            if edge is None:
                raise GraphOpError(f"there is no flow {source} -> {target}")
            graph.edges.remove(edge)
            return
    else:
        is_connect = first_word == "connect" or "->" in line
        if is_connect:
            match = _OP_PATTERNS["connect"].match(line)
            if match:
                source, target, dq, sq, condition = match.groups()
                _connect(graph, source, target, _unquote(dq, sq), condition)
                return
        else:
            match = _OP_PATTERNS["add"].match(line)
            if match:
                node_id, raw_type, dq, sq = match.groups()
                if node_id.lower() not in _OP_KEYWORDS:
                    _add(graph, node_id, normalize_type(raw_type), _unquote(dq, sq) or "")
                    return
    raise GraphOpError(f"could not understand operation: {line}")


def apply_ops(graph: ProcessGraph, lines: list[str]) -> tuple[list[str], list[str]]:
    """Apply operations in place. Returns (applied lines, error messages); invalid operations are skipped."""
    applied: list[str] = []
    errors: list[str] = []
    for raw in lines:
        for line in str(raw).splitlines():
            if not _clean_line(line):
                continue
            try:
                apply_op(graph, line)
                applied.append(_clean_line(line))
            except GraphOpError as exception:
                errors.append(f"`{_clean_line(line)}`: {exception}")
    return applied, errors


def _require_node(graph: ProcessGraph, node_id: str) -> Node:
    node = graph.nodes.get(node_id)
    if node is None:
        raise GraphOpError(f"unknown element id '{node_id}'")
    return node


def _add(graph: ProcessGraph, node_id: str, type_key: str, name: str) -> Node:
    existing = graph.nodes.get(node_id)
    if existing is not None:
        if existing.type == type_key:
            if name:
                existing.name = name
            return existing
        raise GraphOpError(f"id '{node_id}' already exists, pick a new id")
    node = Node(id=node_id, type=type_key, name=name)
    graph.nodes[node_id] = node
    return node


def _connect(graph: ProcessGraph, source: str, target: str, label: str | None, condition: str | None) -> None:
    _require_node(graph, source)
    _require_node(graph, target)
    if source == target:
        raise GraphOpError("an element cannot connect to itself")
    edge = graph.find_edge(source, target)
    if edge is None:
        edge = Edge(source=source, target=target)
        graph.edges.append(edge)
    if label is not None:
        edge.label = label
    if condition is not None:
        edge.condition = condition.strip()


def _insert(graph: ProcessGraph, node_id: str, type_key: str, name: str, source: str, target: str) -> None:
    _require_node(graph, source)
    _require_node(graph, target)
    if node_id in graph.nodes:
        raise GraphOpError(f"id '{node_id}' already exists, pick a new id")
    old_edge = graph.find_edge(source, target)
    _add(graph, node_id, type_key, name)
    first = Edge(source=source, target=node_id)
    if old_edge is not None:
        first.label = old_edge.label
        first.condition = old_edge.condition
        graph.edges.remove(old_edge)
    graph.edges.append(first)
    graph.edges.append(Edge(source=node_id, target=target))


def _remove(graph: ProcessGraph, node_id: str) -> None:
    _require_node(graph, node_id)
    incoming = graph.incoming(node_id)
    outgoing = graph.outgoing(node_id)
    graph.edges = [e for e in graph.edges if node_id not in (e.source, e.target)]
    del graph.nodes[node_id]
    for attached in [n for n in graph.nodes.values() if n.attached_to == node_id]:
        _remove(graph, attached.id)
    # keep a simple chain intact: a -> removed -> b becomes a -> b
    if len(incoming) == 1 and len(outgoing) == 1:
        source, target = incoming[0].source, outgoing[0].target
        if source != target and source in graph.nodes and target in graph.nodes and graph.find_edge(source, target) is None:
            graph.edges.append(Edge(source=source, target=target, label=incoming[0].label, condition=incoming[0].condition))


def validate_graph(graph: ProcessGraph) -> list[str]:
    errors: list[str] = []
    supported = [n for n in graph.nodes.values() if n.supported]
    if not graph.nodes:
        return ["the diagram is empty"]
    starts = [n for n in supported if n.type == "start"]
    if not starts:
        errors.append("add a start event")
    if not [n for n in supported if n.type == "end"]:
        errors.append("add an end event")

    for node in supported:
        incoming = graph.incoming(node.id)
        outgoing = graph.outgoing(node.id)
        if node.type == "start":
            if incoming:
                errors.append(f"start event {node.id} must not have incoming flows")
            if not outgoing:
                errors.append(f"start event {node.id} needs an outgoing flow")
        elif node.type == "end":
            if outgoing:
                errors.append(f"end event {node.id} must not have outgoing flows")
            if not incoming:
                errors.append(f"end event {node.id} needs an incoming flow")
        else:
            if not incoming:
                errors.append(f"{node.id} has no incoming flow")
            if not outgoing:
                errors.append(f"{node.id} has no outgoing flow")
        if node.type in GATEWAY_TYPES and len(incoming) <= 1 and len(outgoing) <= 1:
            errors.append(f"gateway {node.id} must split (2+ outgoing flows) or merge (2+ incoming flows)")
        if node.type not in GATEWAY_TYPES and node.type != "end" and len(outgoing) >= 2:
            errors.append(f"{node.id} has {len(outgoing)} outgoing flows; split with a gateway or disconnect the extra flow")
        if node.type in CONDITIONAL_GATEWAY_TYPES and len(outgoing) >= 2:
            without_condition = [e for e in outgoing if not e.condition]
            if len(without_condition) > 1:
                errors.append(
                    f"gateway {node.id}: every outgoing flow except one default flow needs a condition (`if <python expression>`)"
                )

    reachable = _reachable_from(graph, [n.id for n in starts])
    for node in supported:
        if starts and node.id not in reachable:
            errors.append(f"{node.id} is not reachable from a start event")
    return errors


def _reachable_from(graph: ProcessGraph, start_ids: list[str]) -> set[str]:
    seen: set[str] = set(start_ids)
    queue = deque(start_ids)
    while queue:
        current = queue.popleft()
        neighbours = [e.target for e in graph.outgoing(current)]
        neighbours += [n.id for n in graph.nodes.values() if n.attached_to == current]
        for neighbour in neighbours:
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return seen


def describe_changes(before: ProcessGraph, after: ProcessGraph) -> list[str]:
    changes: list[str] = []
    for node_id, node in after.nodes.items():
        old = before.nodes.get(node_id)
        if old is None:
            changes.append(f"Added {node.label()}")
            continue
        if old.type != node.type:
            changes.append(f"Changed {old.label()} to a {TYPE_LABELS.get(node.type, node.type)}")
        if old.name != node.name:
            changes.append(f'Renamed "{old.name}" to "{node.name}"')
    for node_id, node in before.nodes.items():
        if node_id not in after.nodes:
            changes.append(f"Removed {node.label()}")

    def edge_text(graph: ProcessGraph, edge: Edge) -> str:
        source = graph.nodes[edge.source]
        target = graph.nodes[edge.target]

        def display(node: Node) -> str:
            return node.name or TYPE_LABELS.get(node.type, node.type).capitalize()

        text = f"{display(source)} → {display(target)}"
        if edge.label:
            text += f' ("{edge.label}")'
        return text

    for edge in after.edges:
        old_edge = before.find_edge(edge.source, edge.target)
        if old_edge is None:
            changes.append(f"Connected {edge_text(after, edge)}")
        elif (old_edge.label, old_edge.condition) != (edge.label, edge.condition):
            changes.append(f"Updated flow {edge_text(after, edge)}")
    for edge in before.edges:
        if after.find_edge(edge.source, edge.target) is None and edge.source in after.nodes and edge.target in after.nodes:
            changes.append(f"Disconnected {edge_text(before, edge)}")
    return changes
