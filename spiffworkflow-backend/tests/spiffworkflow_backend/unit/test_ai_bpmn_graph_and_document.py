from pathlib import Path

import pytest
from lxml import etree  # type: ignore

from spiffworkflow_backend.services.ai_bpmn.bpmn_document import BpmnDocument
from spiffworkflow_backend.services.ai_bpmn.bpmn_document import BpmnDocumentError
from spiffworkflow_backend.services.ai_bpmn.graph_ops import GraphOpError
from spiffworkflow_backend.services.ai_bpmn.graph_ops import ProcessGraph
from spiffworkflow_backend.services.ai_bpmn.graph_ops import apply_op
from spiffworkflow_backend.services.ai_bpmn.graph_ops import apply_ops
from spiffworkflow_backend.services.ai_bpmn.graph_ops import describe_changes
from spiffworkflow_backend.services.ai_bpmn.graph_ops import validate_graph
from spiffworkflow_backend.services.custom_parser import MyCustomParser

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
TEMPLATE_PATH = Path(__file__).parents[3] / "src" / "spiffworkflow_backend" / "templates" / "basic_with_user_task_template.bpmn"
APPROVAL_OPS = [
    "add n1 start",
    'add t1 user "Submit request"',
    'add g1 xor "Approved?"',
    'add t2 script "Record request"',
    'add e1 end "Done"',
    'add e2 end "Rejected"',
    "n1 -> t1",
    "t1 -> g1",
    'g1 -> t2 "yes" if approved == True',
    'g1 -> e2 "no"',
    "t2 -> e1",
]


def _approval_graph() -> ProcessGraph:
    graph = ProcessGraph()
    applied, errors = apply_ops(graph, APPROVAL_OPS)
    assert errors == []
    assert len(applied) == len(APPROVAL_OPS)
    return graph


def _parses(bpmn_xml: str, process_id: str) -> None:
    parser = MyCustomParser()
    parser.add_bpmn_str(bpmn_xml.encode("utf-8"))
    assert parser.get_spec(process_id) is not None


class TestGraphOps:
    def test_builds_a_valid_graph_and_outline(self) -> None:
        graph = _approval_graph()
        assert validate_graph(graph) == []
        outline = graph.to_outline()
        assert 't1 user "Submit request"' in outline
        assert 'g1 -> t2 "yes" if approved == True' in outline

    def test_accepts_lenient_syntax(self) -> None:
        graph = ProcessGraph()
        _, errors = apply_ops(graph, ["- add s1 start", "2. add t1 usertask 'Fill form'", "`connect s1 t1`", "", "# comment"])
        assert errors == []
        assert graph.nodes["t1"].type == "user"
        assert graph.find_edge("s1", "t1") is not None

    def test_insert_replaces_the_existing_flow_and_keeps_its_condition(self) -> None:
        graph = _approval_graph()
        apply_op(graph, 'insert t3 user "Check vendor" between g1 t2')
        assert graph.find_edge("g1", "t2") is None
        edge = graph.find_edge("g1", "t3")
        assert edge is not None
        assert edge.condition == "approved == True"
        assert graph.find_edge("t3", "t2") is not None
        assert validate_graph(graph) == []

    def test_remove_reconnects_a_simple_chain(self) -> None:
        graph = _approval_graph()
        apply_op(graph, "remove t2")
        assert "t2" not in graph.nodes
        edge = graph.find_edge("g1", "e1")
        assert edge is not None
        assert edge.label == "yes"

    def test_rename_retype_and_disconnect(self) -> None:
        graph = _approval_graph()
        apply_ops(graph, ['rename t1 "Create request"', "retype t2 service", "disconnect t2 e1"])
        assert graph.nodes["t1"].name == "Create request"
        assert graph.nodes["t2"].type == "service"
        assert graph.find_edge("t2", "e1") is None
        assert "t2 has no outgoing flow" in validate_graph(graph)

    def test_errors_are_reported_and_skipped(self) -> None:
        graph = _approval_graph()
        applied, errors = apply_ops(graph, ["connect t1 zz", "fly away", 'rename t1 "Ok"'])
        assert applied == ['rename t1 "Ok"']
        assert len(errors) == 2
        assert "unknown element id 'zz'" in errors[0]
        with pytest.raises(GraphOpError):
            apply_op(graph, 'add t1 script "Duplicate id with another type"')

    def test_validation_catches_common_modelling_mistakes(self) -> None:
        graph = ProcessGraph()
        apply_ops(
            graph,
            [
                "add s start",
                "add t task",
                "add g xor",
                "add a end",
                "add b end",
                "add orphan task",
                "s -> t",
                "t -> g",
                "g -> a",
                "g -> b",
                "t -> b",
            ],
        )
        problems = "\n".join(validate_graph(graph))
        assert "every outgoing flow except one default flow needs a condition" in problems
        assert "t has 2 outgoing flows" in problems
        assert "orphan has no incoming flow" in problems
        assert "orphan is not reachable" in problems

    def test_describe_changes(self) -> None:
        before = _approval_graph()
        after = before.copy()
        apply_ops(after, ['rename t1 "Create request"', 'insert t3 manual "Call customer" between t2 e1'])
        changes = describe_changes(before, after)
        assert 'Added manual task "Call customer"' in changes
        assert any("Create request" in change and "Submit request" in change for change in changes)


class TestBpmnDocument:
    def test_generates_a_new_diagram_that_spiffworkflow_can_parse(self) -> None:
        document = BpmnDocument.from_xml(None)
        bpmn_xml = document.apply_graph(_approval_graph())

        root = etree.fromstring(bpmn_xml.encode("utf-8"))
        flow_nodes = root.findall(f".//{{{BPMN_NS}}}process/*")
        shapes = root.findall(f".//{{{BPMNDI_NS}}}BPMNShape")
        edges = root.findall(f".//{{{BPMNDI_NS}}}BPMNEdge")
        assert len(shapes) == 6
        assert len(edges) == 5
        assert len(flow_nodes) == 11
        gateway = root.find(f".//{{{BPMN_NS}}}exclusiveGateway")
        default_flow = root.find(f".//{{{BPMN_NS}}}sequenceFlow[@id='{gateway.get('default')}']")
        assert default_flow.get("name") == "no"
        _parses(bpmn_xml, document.process_id)

    def test_edits_preserve_existing_ids_extensions_and_positions(self) -> None:
        original_xml = TEMPLATE_PATH.read_text()
        document = BpmnDocument.from_xml(original_xml)
        assert document.outline().splitlines() == ["n1 start", 'n2 user "Present Form"', "n3 end", "n1 -> n2", "n2 -> n3"]

        graph = document.graph.copy()
        apply_ops(graph, ['insert t1 script "Compute total" between n2 n3', 'rename n2 "Collect order"'])
        bpmn_xml = document.apply_graph(graph)

        root = etree.fromstring(bpmn_xml.encode("utf-8"))
        user_task = root.find(f".//{{{BPMN_NS}}}userTask[@id='present_form']")
        assert user_task is not None
        assert user_task.get("name") == "Collect order"
        assert user_task.find(".//{http://spiffworkflow.org/bpmn/schema/1.0/core}property") is not None
        start_shape = root.find(f".//{{{BPMNDI_NS}}}BPMNShape[@bpmnElement='StartEvent_1']")
        bounds = start_shape[0]
        assert (bounds.get("x"), bounds.get("y")) == ("179", "159")
        script_task = root.find(f".//{{{BPMN_NS}}}scriptTask")
        assert script_task.get("id") == "compute_total"
        assert root.find(f".//{{{BPMNDI_NS}}}BPMNShape[@bpmnElement='compute_total']") is not None
        _parses(bpmn_xml, "natural_language_process_id_template")

        reloaded = BpmnDocument.from_xml(bpmn_xml)
        assert 'script "Compute total"' in reloaded.outline()

    def test_removing_an_element_removes_its_diagram_shape(self) -> None:
        document = BpmnDocument.from_xml(TEMPLATE_PATH.read_text())
        graph = document.graph.copy()
        apply_op(graph, "remove n2")
        bpmn_xml = document.apply_graph(graph)
        assert "present_form" not in bpmn_xml
        _parses(bpmn_xml, "natural_language_process_id_template")

    def test_rejects_invalid_xml(self) -> None:
        with pytest.raises(BpmnDocumentError):
            BpmnDocument.from_xml("<not-bpmn")
