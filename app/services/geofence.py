"""Distance from a GPS point to a route corridor (a polyline), in metres.

Uses a flat-earth projection around the point, which is accurate to well under a
metre over the few kilometres between a vehicle and a nearby road segment.
"""

import math

EARTH_RADIUS_M = 6_371_000.0


def _to_xy(lat: float, lon: float, ref_lat: float, ref_lon: float) -> tuple[float, float]:
    x = math.radians(lon - ref_lon) * EARTH_RADIUS_M * math.cos(math.radians(ref_lat))
    y = math.radians(lat - ref_lat) * EARTH_RADIUS_M
    return x, y


def _point_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def distance_to_corridor_m(lat: float, lon: float, waypoints: list[list[float]]) -> float:
    """Shortest distance from (lat, lon) to the polyline through `waypoints` ([lat, lon] pairs)."""
    if not waypoints:
        raise ValueError("A corridor needs at least one waypoint")
    pts = [_to_xy(w[0], w[1], lat, lon) for w in waypoints]  # the vehicle sits at (0, 0)
    if len(pts) == 1:
        return math.hypot(*pts[0])
    return min(
        _point_segment_distance(0.0, 0.0, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
        for i in range(len(pts) - 1)
    )
