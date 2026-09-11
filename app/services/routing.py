import heapq
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.road_network import (
    IncidentType,
    Intersection,
    Road,
    RoadIncident,
    RoadSegment,
)


@dataclass
class RouteStep:
    from_intersection: str
    to_intersection: str
    road_name: str
    distance_km: float
    travel_minutes: float


@dataclass
class Route:
    steps: list[RouteStep]
    total_distance_km: float = field(default=0.0)
    total_minutes: float = field(default=0.0)

    @property
    def step_count(self) -> int:
        return len(self.steps)


def _build_graph(
    db: Session,
    blocked_segment_ids: set[str] | None = None,
) -> tuple[dict[str, list[tuple[str, RoadSegment]]], dict[str, RoadSegment]]:
    blocked = blocked_segment_ids or set()
    segments = db.query(RoadSegment).all()
    adjacency: dict[str, list[tuple[str, RoadSegment]]] = {}
    seg_lookup: dict[str, RoadSegment] = {}
    for seg in segments:
        key = str(seg.id)
        seg_lookup[key] = seg
        if key in blocked:
            continue
        start = str(seg.start_intersection_id)
        end = str(seg.end_intersection_id)
        # Segments are undirected corridors (two-way roads).
        adjacency.setdefault(start, []).append((end, seg))
        adjacency.setdefault(end, []).append((start, seg))
    return adjacency, seg_lookup


def active_blocking_segments(db: Session, at: datetime | None = None) -> set[str]:
    at = at or datetime.utcnow()
    incidents = (
        db.query(RoadIncident)
        .filter(
            RoadIncident.is_active == True,
            RoadIncident.incident_type.in_(
                [IncidentType.ACCIDENT, IncidentType.ROAD_CLOSED]
            ),
        )
        .all()
    )
    return {str(i.segment_id) for i in incidents if i.segment_id}


def _dijkstra(
    adjacency: dict[str, list[tuple[str, RoadSegment]]],
    origin_id: str,
    destination_id: str,
) -> tuple[float, list[str]]:
    if origin_id == destination_id:
        return 0.0, [origin_id]

    dist: dict[str, float] = {origin_id: 0.0}
    prev: dict[str, tuple[str, str]] = {}
    pq: list[tuple[float, str]] = [(0.0, origin_id)]

    while pq:
        cost, node = heapq.heappop(pq)
        if node == destination_id:
            break
        if cost > dist.get(node, float("inf")):
            continue
        for neighbor, seg in adjacency.get(node, []):
            new_cost = cost + seg.travel_minutes
            if new_cost < dist.get(neighbor, float("inf")):
                dist[neighbor] = new_cost
                prev[neighbor] = (node, str(seg.id))
                heapq.heappush(pq, (new_cost, neighbor))

    if destination_id not in prev and origin_id != destination_id:
        return float("inf"), []

    path: list[str] = []
    node = destination_id
    while node != origin_id:
        path.append(node)
        node = prev[node][0]
    path.append(origin_id)
    path.reverse()
    return dist.get(destination_id, float("inf")), path


def _path_segment_ids(
    path_ids: list[str],
    seg_lookup: dict[str, RoadSegment],
) -> list[str]:
    """Resolve a path of intersection ids into the segment ids used to traverse it."""
    segs_by_endpoint: dict[tuple[str, str], str] = {}
    for key, seg in seg_lookup.items():
        segs_by_endpoint[(str(seg.start_intersection_id), str(seg.end_intersection_id))] = key

    result: list[str] = []
    for i in range(len(path_ids) - 1):
        edge = (path_ids[i], path_ids[i + 1])
        if edge in segs_by_endpoint:
            result.append(segs_by_endpoint[edge])
        else:
            reverse = (path_ids[i + 1], path_ids[i])
            if reverse in segs_by_endpoint:
                result.append(segs_by_endpoint[reverse])
    return result


def _build_route(db: Session, path_ids: list[str]) -> Route:
    route = Route(steps=[])
    if not path_ids:
        return route

    intersections = {str(i.id): i for i in db.query(Intersection).all()}
    road_names = {str(r.id): r.name for r in db.query(Road).all()}
    segments = db.query(RoadSegment).all()
    by_endpoint: dict[tuple[str, str], RoadSegment] = {}
    for seg in segments:
        by_endpoint[(str(seg.start_intersection_id), str(seg.end_intersection_id))] = seg

    for i in range(len(path_ids) - 1):
        from_id, to_id = path_ids[i], path_ids[i + 1]
        seg = by_endpoint.get((from_id, to_id)) or by_endpoint.get((to_id, from_id))
        if seg is None:
            continue
        from_name = intersections.get(from_id).name if from_id in intersections else from_id[:8]
        to_name = intersections.get(to_id).name if to_id in intersections else to_id[:8]
        road_name = road_names.get(str(seg.road_id), "Unknown road")
        route.steps.append(
            RouteStep(
                from_intersection=from_name,
                to_intersection=to_name,
                road_name=road_name,
                distance_km=seg.distance_km,
                travel_minutes=seg.travel_minutes,
            )
        )
        route.total_distance_km += seg.distance_km
        route.total_minutes += seg.travel_minutes
    route.total_minutes = round(route.total_minutes, 1)
    route.total_distance_km = round(route.total_distance_km, 1)
    return route


def find_route(
    db: Session,
    origin_intersection_id: str,
    destination_intersection_id: str,
    avoid_incidents: bool = True,
    extra_blocked: set[str] | None = None,
) -> Route:
    blocked = active_blocking_segments(db) if avoid_incidents else set()
    if extra_blocked:
        blocked |= set(extra_blocked)
    adjacency, seg_lookup = _build_graph(db, blocked)

    cost, path = _dijkstra(adjacency, origin_intersection_id, destination_intersection_id)
    if not path:
        return Route(steps=[])
    return _build_route(db, path)


def find_alternative_routes(
    db: Session,
    origin_intersection_id: str,
    destination_intersection_id: str,
    max_alternatives: int = 2,
    avoid_incidents: bool = True,
) -> list[Route]:
    """Yen-style k-shortest paths: block one edge of the best path at a time
    and recompute, keeping distinct, lowest-cost detours."""
    blocked = active_blocking_segments(db) if avoid_incidents else set()
    adjacency, seg_lookup = _build_graph(db, blocked)

    _cost, best_path = _dijkstra(adjacency, origin_intersection_id, destination_intersection_id)
    if not best_path:
        return []

    routes: list[Route] = [_build_route(db, best_path)]
    edges = _path_segment_ids(best_path, seg_lookup)

    for edge_id in edges:
        if len(routes) > max_alternatives:
            break
        adj2, _ = _build_graph(db, blocked | {edge_id})
        _c2, p2 = _dijkstra(adj2, origin_intersection_id, destination_intersection_id)
        if not p2:
            continue
        candidate = _build_route(db, p2)
        new_steps = [(s.from_intersection, s.to_intersection) for s in candidate.steps]
        dup = any(
            [(s.from_intersection, s.to_intersection) for s in r.steps] == new_steps
            for r in routes
        )
        if dup:
            continue
        routes.append(candidate)

    return routes