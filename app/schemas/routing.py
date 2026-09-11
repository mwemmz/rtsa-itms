from pydantic import BaseModel


class RouteStepOut(BaseModel):
    from_intersection: str
    to_intersection: str
    road_name: str
    distance_km: float
    travel_minutes: float


class RouteOut(BaseModel):
    total_distance_km: float
    total_minutes: float
    step_count: int
    steps: list[RouteStepOut]


class RouteResult(BaseModel):
    origin: str
    destination: str
    primary_route: RouteOut | None
    alternatives: list[RouteOut] = []
    incidents_avoided: int = 0