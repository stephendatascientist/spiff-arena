"""Converts between BPMN XML and the compact ProcessGraph, preserving everything the assistant did not touch."""

from __future__ import annotations

import random
import re
import string

from lxml import etree  # type: ignore

from spiffworkflow_backend.services.ai_bpmn.bpmn_layout import Bounds
from spiffworkflow_backend.services.ai_bpmn.bpmn_layout import incremental_layout
from spiffworkflow_backend.services.ai_bpmn.bpmn_layout import is_gateway
from spiffworkflow_backend.services.ai_bpmn.bpmn_layout import layered_layout
from spiffworkflow_backend.services.ai_bpmn.bpmn_layout import route_edge
from spiffworkflow_backend.services.ai_bpmn.graph_ops import CONDITIONAL_GATEWAY_TYPES
from spiffworkflow_backend.services.ai_bpmn.graph_ops import NODE_TYPES
from spiffworkflow_backend.services.ai_bpmn.graph_ops import TAG_TO_TYPE
from spiffworkflow_backend.services.ai_bpmn.graph_ops import Edge
from spiffworkflow_backend.services.ai_bpmn.graph_ops import Node
from spiffworkflow_backend.services.ai_bpmn.graph_ops import ProcessGraph

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
DC_NS = "http://www.omg.org/spec/DD/20100524/DC"
DI_NS = "http://www.omg.org/spec/DD/20100524/DI"
SPIFF_NS = "http://spiffworkflow.org/bpmn/schema/1.0/core"
NSMAP = {"bpmn": BPMN_NS, "bpmndi": BPMNDI_NS, "dc": DC_NS, "di": DI_NS, "spiffworkflow": SPIFF_NS}

FLOW_NODE_TAGS = {
    "startEvent",
    "endEvent",
    "intermediateCatchEvent",
    "intermediateThrowEvent",
    "boundaryEvent",
    "task",
    "userTask",
    "manualTask",
    "scriptTask",
    "serviceTask",
    "sendTask",
    "receiveTask",
    "businessRuleTask",
    "callActivity",
    "subProcess",
    "transaction",
    "adHocSubProcess",
    "exclusiveGateway",
    "parallelGateway",
    "inclusiveGateway",
    "eventBasedGateway",
    "complexGateway",
}
# children that only make sense for a specific element type and are dropped when the type changes
TYPE_SPECIFIC_CHILDREN = {"scriptTask": {"script"}, "callActivity": set()}
ID_PREFIXES = {"start": "StartEvent", "end": "EndEvent", "xor": "Gateway", "and": "Gateway", "or": "Gateway"}


class BpmnDocumentError(Exception):
    pass


def _q(namespace: str, tag: str) -> str:
    return f"{{{namespace}}}{tag}"


def _local(element: etree._Element) -> str:
    return str(etree.QName(element).localname)


def _random_suffix() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=7))  # noqa: S311


class BpmnDocument:
    """A BPMN file opened for AI editing. `graph` is the editable compact view of one process."""

    def __init__(self, root: etree._Element, process: etree._Element) -> None:
        self.root = root
        self.process = process
        self.graph = ProcessGraph()
        self._node_elements: dict[str, etree._Element] = {}
        self._edge_elements: dict[str, etree._Element] = {}
        self._load_graph()

    @classmethod
    def from_xml(cls, bpmn_xml: str | None, process_id: str | None = None) -> BpmnDocument:
        if not bpmn_xml or not bpmn_xml.strip():
            return cls.new(process_id)
        parser = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=True)
        try:
            root = etree.fromstring(bpmn_xml.encode("utf-8"), parser=parser)  # noqa: S320
        except etree.XMLSyntaxError as exception:
            raise BpmnDocumentError(f"Could not parse the BPMN XML: {exception}") from exception
        if root.tag != _q(BPMN_NS, "definitions"):
            raise BpmnDocumentError("The XML is not a BPMN definitions document.")
        processes = root.findall(_q(BPMN_NS, "process"))
        process = None
        if process_id:
            process = next((p for p in processes if p.get("id") == process_id), None)
        if process is None:
            process = next((p for p in processes if p.get("isExecutable") == "true"), None)
        if process is None and processes:
            process = processes[0]
        if process is None:
            process = etree.Element(_q(BPMN_NS, "process"), id=process_id or f"Process_{_random_suffix()}")
            process.set("isExecutable", "true")
            first_diagram = root.find(_q(BPMNDI_NS, "BPMNDiagram"))
            if first_diagram is not None:
                first_diagram.addprevious(process)
            else:
                root.append(process)
        return cls(root, process)

    @classmethod
    def new(cls, process_id: str | None = None) -> BpmnDocument:
        root = etree.Element(_q(BPMN_NS, "definitions"), nsmap=NSMAP)
        root.set("id", f"Definitions_{_random_suffix()}")
        root.set("targetNamespace", "http://bpmn.io/schema/bpmn")
        process = etree.SubElement(root, _q(BPMN_NS, "process"), id=process_id or f"Process_{_random_suffix()}")
        process.set("isExecutable", "true")
        return cls(root, process)

    @property
    def process_id(self) -> str:
        return str(self.process.get("id"))

    def _load_graph(self) -> None:
        handle_for_bpmn_id: dict[str, str] = {}
        for element in self.process:
            if not isinstance(element.tag, str) or _local(element) not in FLOW_NODE_TAGS:
                continue
            bpmn_id = element.get("id")
            if not bpmn_id:
                continue
            handle = f"n{len(self.graph.nodes) + 1}"
            tag = _local(element)
            node = Node(id=handle, type=TAG_TO_TYPE.get(tag, tag), name=element.get("name", ""), bpmn_id=bpmn_id)
            self.graph.nodes[handle] = node
            self._node_elements[handle] = element
            handle_for_bpmn_id[bpmn_id] = handle
        for node_id, element in self._node_elements.items():
            attached_to = element.get("attachedToRef")
            if attached_to in handle_for_bpmn_id:
                self.graph.nodes[node_id].attached_to = handle_for_bpmn_id[attached_to]

        for element in self.process.findall(_q(BPMN_NS, "sequenceFlow")):
            source = handle_for_bpmn_id.get(element.get("sourceRef", ""))
            target = handle_for_bpmn_id.get(element.get("targetRef", ""))
            if source is None or target is None:
                continue
            condition_element = element.find(_q(BPMN_NS, "conditionExpression"))
            condition = (condition_element.text or "").strip() if condition_element is not None else ""
            edge = Edge(
                source=source, target=target, label=element.get("name", ""), condition=condition, bpmn_id=element.get("id")
            )
            self.graph.edges.append(edge)
            self._edge_elements[f"{source}>{target}"] = element

    def outline(self) -> str:
        return self.graph.to_outline()

    def apply_graph(self, new_graph: ProcessGraph) -> str:
        """Merge new_graph into the XML (keeping untouched elements as they are) and return the resulting XML."""
        old_graph = self.graph
        old_flows = {(e.source, e.target) for e in old_graph.edges}
        new_flows = {(e.source, e.target) for e in new_graph.edges}
        structure_changed = set(old_graph.nodes) != set(new_graph.nodes) or old_flows != new_flows
        used_ids = {str(element_id) for element_id in self.root.xpath("//@id")}

        for handle in [h for h in old_graph.nodes if h not in new_graph.nodes]:
            self._remove_element(self._node_elements.pop(handle))
        for edge in old_graph.edges:
            key = f"{edge.source}>{edge.target}"
            if new_graph.find_edge(edge.source, edge.target) is None and key in self._edge_elements:
                self._remove_element(self._edge_elements.pop(key))

        bpmn_ids: dict[str, str] = {}
        for handle, node in new_graph.nodes.items():
            element = self._node_elements.get(handle)
            if element is None:
                bpmn_id = self._new_id(node, used_ids)
                element = etree.SubElement(self.process, _q(BPMN_NS, NODE_TYPES[node.type]), id=bpmn_id)
                self._node_elements[handle] = element
                node.bpmn_id = bpmn_id
            elif node.supported and _local(element) != NODE_TYPES[node.type]:
                self._change_type(element, NODE_TYPES[node.type])
            if node.name:
                element.set("name", node.name)
            elif "name" in element.attrib:
                del element.attrib["name"]
            bpmn_ids[handle] = str(element.get("id"))

        for edge in new_graph.edges:
            key = f"{edge.source}>{edge.target}"
            element = self._edge_elements.get(key)
            if element is None:
                flow_id = self._unique_id("Flow", used_ids, random_suffix=True)
                element = etree.SubElement(self.process, _q(BPMN_NS, "sequenceFlow"), id=flow_id)
                element.set("sourceRef", bpmn_ids[edge.source])
                element.set("targetRef", bpmn_ids[edge.target])
                self._edge_elements[key] = element
                edge.bpmn_id = flow_id
            self._set_flow_details(element, edge)

        self._rebuild_incoming_outgoing()
        self._update_gateway_defaults(new_graph)
        self._update_diagram(new_graph, bpmn_ids, structure_changed)
        self.graph = new_graph
        return self.to_xml()

    def to_xml(self) -> str:
        etree.indent(self.root, space="  ")
        return str(etree.tostring(self.root, xml_declaration=True, encoding="UTF-8", pretty_print=True).decode("utf-8"))

    def _new_id(self, node: Node, used_ids: set[str]) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", node.name.lower()).strip("_")[:40] if node.name else ""
        if slug and not slug[0].isdigit():
            return self._unique_id(slug, used_ids)
        prefix = ID_PREFIXES.get(node.type, "Activity")
        return self._unique_id(prefix, used_ids, random_suffix=True)

    @staticmethod
    def _unique_id(base: str, used_ids: set[str], random_suffix: bool = False) -> str:
        candidate = f"{base}_{_random_suffix()}" if random_suffix else base
        counter = 2
        while candidate in used_ids:
            candidate = f"{base}_{_random_suffix()}" if random_suffix else f"{base}_{counter}"
            counter += 1
        used_ids.add(candidate)
        return candidate

    def _change_type(self, element: etree._Element, new_tag: str) -> None:
        old_tag = _local(element)
        for child_tag in TYPE_SPECIFIC_CHILDREN.get(old_tag, set()):
            for child in element.findall(_q(BPMN_NS, child_tag)):
                element.remove(child)
        if old_tag == "callActivity" and "calledElement" in element.attrib:
            del element.attrib["calledElement"]
        if old_tag in ("exclusiveGateway", "inclusiveGateway") and "default" in element.attrib:
            del element.attrib["default"]
        element.tag = _q(BPMN_NS, new_tag)

    def _set_flow_details(self, element: etree._Element, edge: Edge) -> None:
        if edge.label:
            element.set("name", edge.label)
        elif "name" in element.attrib:
            del element.attrib["name"]
        condition_element = element.find(_q(BPMN_NS, "conditionExpression"))
        if edge.condition:
            if condition_element is None:
                condition_element = etree.SubElement(element, _q(BPMN_NS, "conditionExpression"))
            condition_element.text = edge.condition
        elif condition_element is not None:
            element.remove(condition_element)

    def _remove_element(self, element: etree._Element) -> None:
        element_id = element.get("id")
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
        if not element_id:
            return
        for ref in self.root.xpath("//bpmn:flowNodeRef[text()=$element_id]", namespaces={"bpmn": BPMN_NS}, element_id=element_id):
            ref.getparent().remove(ref)
        for association in self.root.xpath(
            "//bpmn:association[@sourceRef=$element_id or @targetRef=$element_id]",
            namespaces={"bpmn": BPMN_NS},
            element_id=element_id,
        ):
            self._remove_element(association)
        for gateway in self.process.xpath("./*[@default=$element_id]", element_id=element_id):
            del gateway.attrib["default"]
        for di_element in self._di_elements_for(element_id):
            di_element.getparent().remove(di_element)

    def _di_elements_for(self, element_id: str) -> list[etree._Element]:
        return list(
            self.root.xpath(
                "//bpmndi:BPMNShape[@bpmnElement=$element_id] | //bpmndi:BPMNEdge[@bpmnElement=$element_id]",
                namespaces={"bpmndi": BPMNDI_NS},
                element_id=element_id,
            )
        )

    def _rebuild_incoming_outgoing(self) -> None:
        incoming: dict[str, list[str]] = {}
        outgoing: dict[str, list[str]] = {}
        for flow in self.process.findall(_q(BPMN_NS, "sequenceFlow")):
            outgoing.setdefault(flow.get("sourceRef", ""), []).append(flow.get("id", ""))
            incoming.setdefault(flow.get("targetRef", ""), []).append(flow.get("id", ""))
        for element in self.process:
            if not isinstance(element.tag, str) or _local(element) not in FLOW_NODE_TAGS:
                continue
            for child in element.findall(_q(BPMN_NS, "incoming")) + element.findall(_q(BPMN_NS, "outgoing")):
                element.remove(child)
            position = sum(
                1 for child in element if isinstance(child.tag, str) and _local(child) in ("documentation", "extensionElements")
            )
            element_id = element.get("id", "")
            references = [("incoming", flow_id) for flow_id in incoming.get(element_id, [])]
            references += [("outgoing", flow_id) for flow_id in outgoing.get(element_id, [])]
            for offset, (tag, flow_id) in enumerate(references):
                reference = etree.Element(_q(BPMN_NS, tag))
                reference.text = flow_id
                element.insert(position + offset, reference)

    def _update_gateway_defaults(self, graph: ProcessGraph) -> None:
        for handle, node in graph.nodes.items():
            element = self._node_elements[handle]
            default_flow = element.get("default")
            if default_flow and not self.process.xpath(
                "./bpmn:sequenceFlow[@id=$flow_id]", namespaces={"bpmn": BPMN_NS}, flow_id=default_flow
            ):
                del element.attrib["default"]
                default_flow = None
            if node.type not in CONDITIONAL_GATEWAY_TYPES:
                continue
            outgoing = graph.outgoing(handle)
            without_condition = [e for e in outgoing if not e.condition]
            if len(outgoing) >= 2 and len(without_condition) == 1:
                element.set("default", str(self._edge_elements[f"{handle}>{without_condition[0].target}"].get("id")))
            elif default_flow and not without_condition:
                del element.attrib["default"]

    def _plane(self) -> etree._Element:
        candidates = {self.process_id}
        for participant in self.root.xpath(
            "//bpmn:participant[@processRef=$pid]", namespaces={"bpmn": BPMN_NS}, pid=self.process_id
        ):
            collaboration = participant.getparent()
            if collaboration is not None and collaboration.get("id"):
                candidates.add(str(collaboration.get("id")))
        for plane in self.root.iter(_q(BPMNDI_NS, "BPMNPlane")):
            if plane.get("bpmnElement") in candidates:
                return plane
        diagram = etree.SubElement(self.root, _q(BPMNDI_NS, "BPMNDiagram"), id=f"BPMNDiagram_{_random_suffix()}")
        return etree.SubElement(
            diagram, _q(BPMNDI_NS, "BPMNPlane"), id=f"BPMNPlane_{_random_suffix()}", bpmnElement=self.process_id
        )

    def _uses_complex_layout(self, plane: etree._Element) -> bool:
        has_lanes = self.process.find(_q(BPMN_NS, "laneSet")) is not None
        has_expanded = any(shape.get("isExpanded") == "true" for shape in plane.iter(_q(BPMNDI_NS, "BPMNShape")))
        return has_lanes or has_expanded

    def _update_diagram(self, graph: ProcessGraph, bpmn_ids: dict[str, str], structure_changed: bool) -> None:
        plane = self._plane()
        shapes: dict[str, etree._Element] = {}
        for shape in plane.findall(_q(BPMNDI_NS, "BPMNShape")):
            shapes[shape.get("bpmnElement", "")] = shape
        existing: dict[str, Bounds] = {}
        for handle, bpmn_id in bpmn_ids.items():
            shape = shapes.get(bpmn_id)
            bounds_element = shape.find(_q(DC_NS, "Bounds")) if shape is not None else None
            if bounds_element is not None:
                existing[handle] = Bounds(
                    float(bounds_element.get("x", 0)),
                    float(bounds_element.get("y", 0)),
                    float(bounds_element.get("width", 100)),
                    float(bounds_element.get("height", 80)),
                )

        preserved = [h for h in existing if h in self.graph.nodes]
        new_nodes = [h for h in graph.nodes if h not in existing]
        if not structure_changed and not new_nodes:
            return
        full_layout = not self._uses_complex_layout(plane) and len(new_nodes) >= len(preserved)
        if full_layout:
            positions = layered_layout(graph)
            moved = set(positions)
        else:
            positions, moved = incremental_layout(graph, existing)

        for handle in moved:
            if handle not in positions:
                continue
            bpmn_id = bpmn_ids[handle]
            shape = shapes.get(bpmn_id)
            if shape is None:
                shape = etree.SubElement(plane, _q(BPMNDI_NS, "BPMNShape"), id=f"{bpmn_id}_di", bpmnElement=bpmn_id)
                shapes[bpmn_id] = shape
            self._write_bounds(shape, positions[handle])

        lowest_y = max((b.bottom for b in positions.values()), default=0)
        obstacles = [b for h, b in positions.items() if not graph.nodes[h].attached_to]
        edges_by_flow = {e.get("bpmnElement"): e for e in plane.findall(_q(BPMNDI_NS, "BPMNEdge"))}
        for edge in graph.edges:
            flow_id = str(self._edge_elements[f"{edge.source}>{edge.target}"].get("id"))
            di_edge = edges_by_flow.get(flow_id)
            if di_edge is not None and edge.source not in moved and edge.target not in moved:
                continue
            if edge.source not in positions or edge.target not in positions:
                continue
            if di_edge is None:
                di_edge = etree.SubElement(plane, _q(BPMNDI_NS, "BPMNEdge"), id=f"{flow_id}_di", bpmnElement=flow_id)
            for child in list(di_edge):
                di_edge.remove(child)
            waypoints = route_edge(
                positions[edge.source],
                positions[edge.target],
                is_gateway(graph.nodes[edge.source].type),
                is_gateway(graph.nodes[edge.target].type),
                lowest_y,
                obstacles,
            )
            for x, y in waypoints:
                etree.SubElement(di_edge, _q(DI_NS, "waypoint"), x=_fmt(x), y=_fmt(y))

    @staticmethod
    def _write_bounds(shape: etree._Element, bounds: Bounds) -> None:
        for child in list(shape):
            shape.remove(child)
        etree.SubElement(
            shape, _q(DC_NS, "Bounds"), x=_fmt(bounds.x), y=_fmt(bounds.y), width=_fmt(bounds.width), height=_fmt(bounds.height)
        )


def _fmt(value: float) -> str:
    return str(int(round(value)))
