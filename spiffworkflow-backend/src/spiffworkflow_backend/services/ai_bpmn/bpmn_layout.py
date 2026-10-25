"""Deterministic layout for AI generated BPMN diagrams (no LLM involved)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from spiffworkflow_backend.services.ai_bpmn.graph_ops import GATEWAY_TYPES
from spiffworkflow_backend.services.ai_bpmn.graph_ops import ProcessGraph

EVENT_TYPES = {"start", "end", "startEvent", "endEvent", "intermediateCatchEvent", "intermediateThrowEvent", "boundaryEvent"}
GATEWAY_TAGS = {"exclusiveGateway", "parallelGateway", "inclusiveGateway", "eventBasedGateway", "complexGateway"}

COLUMN_WIDTH = 170
ROW_HEIGHT = 130
ORIGIN_X = 180
ORIGIN_Y = 160
GAP_X = 60


@dataclass
class Bounds:
    x: float
    y: float
    width: float
    height: float

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def overlaps(self, other: Bounds, margin: float = 20) -> bool:
        return not (
            self.right + margin <= other.x
            or other.right + margin <= self.x
            or self.bottom + margin <= other.y
            or other.bottom + margin <= self.y
        )


def is_gateway(node_type: str) -> bool:
    return node_type in GATEWAY_TYPES or node_type in GATEWAY_TAGS


def shape_size(node_type: str) -> tuple[float, float]:
    if node_type in EVENT_TYPES:
        return 36, 36
    if is_gateway(node_type):
        return 50, 50
    return 100, 80


def _bounds_at(node_type: str, center_x: float, center_y: float) -> Bounds:
    width, height = shape_size(node_type)
    return Bounds(center_x - width / 2, center_y - height / 2, width, height)


def _flow_nodes(graph: ProcessGraph) -> list[str]:
    return [n.id for n in graph.nodes.values() if not n.attached_to]


def _layout_source(graph: ProcessGraph, node_id: str) -> str:
    attached_to = graph.nodes[node_id].attached_to
    return attached_to if attached_to and attached_to in graph.nodes else node_id


def _forward_edges(graph: ProcessGraph) -> list[tuple[str, str]]:
    """Edges between top level nodes (boundary events count as their host) with cycles broken by DFS."""
    nodes = _flow_nodes(graph)
    successors: dict[str, list[str]] = {n: [] for n in nodes}
    for edge in graph.edges:
        source = _layout_source(graph, edge.source)
        if source in successors and edge.target in successors and source != edge.target:
            successors[source].append(edge.target)

    roots = [n for n in nodes if graph.nodes[n].type in ("start", "startEvent")]
    roots += [n for n in nodes if n not in roots]
    state: dict[str, int] = {}
    forward: list[tuple[str, str]] = []
    for root in roots:
        if root in state:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        state[root] = 1
        while stack:
            current, index = stack.pop()
            if index < len(successors[current]):
                stack.append((current, index + 1))
                child = successors[current][index]
                if state.get(child) == 1:
                    continue  # back edge
                forward.append((current, child))
                if child not in state:
                    state[child] = 1
                    stack.append((child, 0))
            else:
                state[current] = 2
    return forward


def layered_layout(graph: ProcessGraph) -> dict[str, Bounds]:
    """Left-to-right layout: columns by longest path, rows so that gateway branches stack vertically."""
    nodes = _flow_nodes(graph)
    forward = _forward_edges(graph)
    predecessors: dict[str, list[str]] = {n: [] for n in nodes}
    successors: dict[str, list[str]] = {n: [] for n in nodes}
    for source, target in forward:
        if source not in successors[target]:
            successors[source].append(target)
            predecessors[target].append(source)

    in_degree = {n: len(predecessors[n]) for n in nodes}
    queue = deque([n for n in nodes if in_degree[n] == 0])
    order: list[str] = []
    while queue:
        current = queue.popleft()
        order.append(current)
        for child in successors[current]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)
    order += [n for n in nodes if n not in order]

    column: dict[str, int] = {}
    for node_id in order:
        column[node_id] = max((column[p] + 1 for p in predecessors[node_id] if p in column), default=0)

    row: dict[str, int] = {}
    preferred_row: dict[str, int] = {}
    occupied: dict[int, set[int]] = {}
    next_free_top_row = 0
    for node_id in order:
        if node_id in preferred_row:
            desired = preferred_row[node_id]
        elif predecessors[node_id]:
            desired = min(row[p] for p in predecessors[node_id] if p in row)
        else:
            desired = next_free_top_row
        taken = occupied.setdefault(column[node_id], set())
        while desired in taken:
            desired += 1
        taken.add(desired)
        row[node_id] = desired
        if not predecessors[node_id]:
            next_free_top_row = max(next_free_top_row, desired + 1)
        for index, child in enumerate(successors[node_id]):
            if child not in preferred_row and len(predecessors[child]) == 1:
                preferred_row[child] = desired + index

    positions = {
        node_id: _bounds_at(
            graph.nodes[node_id].type, ORIGIN_X + column[node_id] * COLUMN_WIDTH, ORIGIN_Y + row[node_id] * ROW_HEIGHT
        )
        for node_id in nodes
    }
    _place_boundary_events(graph, positions)
    return positions


def _place_boundary_events(graph: ProcessGraph, positions: dict[str, Bounds]) -> None:
    per_host: dict[str, int] = {}
    for node in graph.nodes.values():
        if not node.attached_to or node.attached_to not in positions or node.id in positions:
            continue
        host = positions[node.attached_to]
        index = per_host.get(node.attached_to, 0)
        per_host[node.attached_to] = index + 1
        positions[node.id] = Bounds(host.right - 28 - index * 40, host.bottom - 18, 36, 36)


def incremental_layout(graph: ProcessGraph, existing: dict[str, Bounds]) -> tuple[dict[str, Bounds], set[str]]:
    """Keep existing positions; place new nodes next to their neighbours, shifting downstream shapes to make room.

    Returns the positions and the ids of nodes that moved or were added.
    """
    positions = {k: Bounds(v.x, v.y, v.width, v.height) for k, v in existing.items() if k in graph.nodes}
    moved: set[str] = set()
    pending = [n for n in _flow_nodes(graph) if n not in positions]

    progress = True
    while pending and progress:
        progress = False
        for node_id in list(pending):
            node_type = graph.nodes[node_id].type
            width, height = shape_size(node_type)
            predecessor = next(
                (
                    positions[_layout_source(graph, e.source)]
                    for e in graph.incoming(node_id)
                    if _layout_source(graph, e.source) in positions
                ),
                None,
            )
            successor = next((positions[e.target] for e in graph.outgoing(node_id) if e.target in positions), None)
            if predecessor is not None:
                x = predecessor.right + GAP_X
                bounds = Bounds(x, predecessor.center_y - height / 2, width, height)
                if successor is not None and successor.x < bounds.right + GAP_X and successor.center_x > predecessor.center_x:
                    _shift_right_of(positions, predecessor.center_x, width + GAP_X, moved)
            elif successor is not None:
                bounds = Bounds(successor.x - GAP_X - width, successor.center_y - height / 2, width, height)
                if bounds.x < 0:
                    _shift_right_of(positions, successor.x - 1, width + GAP_X, moved)
                    bounds.x = successor.x - GAP_X - width
            else:
                continue
            _move_until_free(bounds, positions)
            positions[node_id] = bounds
            moved.add(node_id)
            pending.remove(node_id)
            progress = True

    for node_id in pending:
        width, height = shape_size(graph.nodes[node_id].type)
        lowest = max((b.bottom for b in positions.values()), default=ORIGIN_Y)
        bounds = Bounds(ORIGIN_X, lowest + ROW_HEIGHT / 2, width, height)
        _move_until_free(bounds, positions)
        positions[node_id] = bounds
        moved.add(node_id)

    before_boundary = set(positions)
    _place_boundary_events(graph, positions)
    moved |= set(positions) - before_boundary
    for node in graph.nodes.values():
        if node.attached_to in moved and node.id in positions and node.id not in moved:
            host = positions[node.attached_to]
            positions[node.id].x = host.right - 28
            positions[node.id].y = host.bottom - 18
            moved.add(node.id)
    return positions, moved


def _shift_right_of(positions: dict[str, Bounds], threshold_x: float, delta: float, moved: set[str]) -> None:
    for node_id, bounds in positions.items():
        if bounds.center_x > threshold_x:
            bounds.x += delta
            moved.add(node_id)


def _move_until_free(bounds: Bounds, positions: dict[str, Bounds]) -> None:
    for _ in range(50):
        if not any(bounds.overlaps(other) for other in positions.values()):
            return
        bounds.y += ROW_HEIGHT


def _segment_hits(start: tuple[float, float], end: tuple[float, float], box: Bounds) -> bool:
    min_x, max_x = sorted((start[0], end[0]))
    min_y, max_y = sorted((start[1], end[1]))
    return min_x < box.right and max_x > box.x and min_y < box.bottom and max_y > box.y


def _path_hits(points: list[tuple[float, float]], obstacles: list[Bounds]) -> bool:
    return any(_segment_hits(points[i], points[i + 1], box) for i in range(len(points) - 1) for box in obstacles)


def route_edge(
    source: Bounds,
    target: Bounds,
    source_is_gateway: bool,
    target_is_gateway: bool,
    lowest_y: float,
    obstacles: list[Bounds] | None = None,
) -> list[tuple[float, float]]:
    """Orthogonal waypoints between two shapes, detouring below or above shapes in the way."""
    points = _direct_route(source, target, source_is_gateway, target_is_gateway, lowest_y)
    others = [b for b in (obstacles or []) if b is not source and b is not target]
    if not others or not _path_hits(points, others):
        return points
    left, right = sorted((source.center_x, target.center_x))
    in_range = [b for b in others if b.right > left and b.x < right]
    under_y = max([b.bottom for b in in_range] + [source.bottom, target.bottom]) + 30
    under = [
        (source.center_x, source.bottom),
        (source.center_x, under_y),
        (target.center_x, under_y),
        (target.center_x, target.bottom),
    ]
    over_y = min([b.y for b in in_range] + [source.y, target.y]) - 30
    over = [(source.center_x, source.y), (source.center_x, over_y), (target.center_x, over_y), (target.center_x, target.y)]
    for candidate in (under, over):
        if not _path_hits(candidate, others):
            return candidate
    return under


def _direct_route(
    source: Bounds, target: Bounds, source_is_gateway: bool, target_is_gateway: bool, lowest_y: float
) -> list[tuple[float, float]]:
    if target.center_x <= source.center_x:
        below = max(lowest_y, source.bottom, target.bottom) + 40
        return [
            (source.center_x, source.bottom),
            (source.center_x, below),
            (target.center_x, below),
            (target.center_x, target.bottom),
        ]
    if abs(source.center_y - target.center_y) < 1:
        return [(source.right, source.center_y), (target.x, target.center_y)]
    if source_is_gateway:
        start_y = source.bottom if target.center_y > source.center_y else source.y
        return [(source.center_x, start_y), (source.center_x, target.center_y), (target.x, target.center_y)]
    if target_is_gateway:
        end_y = target.bottom if source.center_y > target.center_y else target.y
        return [(source.right, source.center_y), (target.center_x, source.center_y), (target.center_x, end_y)]
    middle_x = (source.right + target.x) / 2
    return [
        (source.right, source.center_y),
        (middle_x, source.center_y),
        (middle_x, target.center_y),
        (target.x, target.center_y),
    ]
