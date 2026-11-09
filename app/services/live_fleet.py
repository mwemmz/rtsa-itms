"""Simulated live fleet: a set of registered vehicles "driving" along real Zambian routes.

There is no GPS hardware in this demo, so positions are generated. They are a pure function of the clock
(no background thread, nothing stored), so every browser and every server worker sees the same vehicle at the
same place at the same moment. Which vehicles drive, their routes and speeds, and the compliance facts shown
for each come from the real database; only the movement is simulated.
"""

from __future__ import annotations

import hashlib
import json
import math
import time

from sqlalchemy.orm import Session

from app.models.psv import PSVCorridor, PSVOperator, PSVPermit, PSVPermitStatus
from app.models.vehicle import Vehicle, VehicleStatus
from app.services.compliance import check_vehicle_compliance
from app.services.geofence import distance_to_corridor_m

MAX_VEHICLES = 14
TIME_SCALE = 15.0       # demo time runs this many times faster than the clock, so movement is visible on screen
DWELL_S = 300.0         # simulated seconds a vehicle waits at each end of its route (20 real seconds)
CACHE_S = 60.0

# [lat, lon] waypoints along real corridors (approximate, for the demo)
ROUTES: list[tuple[str, list[list[float]]]] = [
    ("Great North Road: Lusaka to Kabwe", [[-15.4167, 28.2833], [-15.2, 28.3], [-14.9, 28.4], [-14.45, 28.45]]),
    ("Livingstone Road: Lusaka to Kafue", [[-15.4167, 28.2833], [-15.6, 28.2], [-15.77, 28.18], [-16.05, 27.95]]),
    ("Great East Road: Lusaka to Chongwe", [[-15.4167, 28.2833], [-15.3945, 28.335], [-15.33, 28.68], [-15.2, 29.0]]),
    ("Cairo Road, Lusaka", [[-15.4300, 28.2800], [-15.4167, 28.2833], [-15.3950, 28.3000], [-15.3750, 28.3200]]),
    ("Independence Avenue to Kalingalinga", [[-15.4100, 28.2850], [-15.4000, 28.3100], [-15.3950, 28.3400], [-15.3900, 28.3600]]),
    ("Mumbwa Road, Lusaka", [[-15.4000, 28.2700], [-15.3800, 28.2000], [-15.3500, 28.1200], [-15.2900, 27.9500]]),
]

_cache: dict = {"t": 0.0, "fleet": []}


def _xy(lat: float, lon: float, lat0: float) -> tuple[float, float]:
    return math.radians(lon) * 6_371_000.0 * math.cos(math.radians(lat0)), math.radians(lat) * 6_371_000.0


def _h(text: str, n: int) -> int:
    return int(hashlib.sha1(text.encode()).hexdigest()[:8], 16) % n


class _Path:
    def __init__(self, pts: list[list[float]]):
        self.pts = pts
        lat0 = pts[0][0]
        self.k = 111_320.0 * math.cos(math.radians(lat0))
        self.cum = [0.0]
        for a, b in zip(pts, pts[1:]):
            self.cum.append(self.cum[-1] + math.hypot((b[1] - a[1]) * self.k, (b[0] - a[0]) * 111_320.0))
        self.length = self.cum[-1] or 1.0

    def at(self, s: float) -> tuple[float, float, float]:
        """(lat, lon, bearing_deg) at distance s along the path."""
        s = max(0.0, min(self.length, s))
        i = 1
        while i < len(self.cum) - 1 and self.cum[i] < s:
            i += 1
        a, b = self.pts[i - 1], self.pts[i]
        seg = (self.cum[i] - self.cum[i - 1]) or 1.0
        f = (s - self.cum[i - 1]) / seg
        lat, lon = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        brg = math.degrees(math.atan2((b[1] - a[1]) * self.k, (b[0] - a[0]) * 111_320.0)) % 360
        return lat, lon, brg


def _build_fleet(db: Session) -> list[dict]:
    vehicles = db.query(Vehicle).filter(Vehicle.status == VehicleStatus.ACTIVE).order_by(Vehicle.registration_number).all()
    permits = {p.vehicle_id: p for p in db.query(PSVPermit).filter(PSVPermit.status == PSVPermitStatus.ACTIVE).all()}
    corridors = {c.permit_id: c for c in db.query(PSVCorridor).all()}
    operators = {o.id: o for o in db.query(PSVOperator).all()}
    # vehicles with a PSV route first, then the rest, capped so the map stays readable
    ordered = sorted(vehicles, key=lambda v: (v.id not in permits, v.registration_number))[:MAX_VEHICLES]
    fleet = []
    for n, v in enumerate(ordered):
        permit = permits.get(v.id)
        corridor = corridors.get(permit.id) if permit else None
        pts, label, buffer_m = None, None, None
        if corridor:
            try:
                raw = json.loads(corridor.waypoints)
                pts = [[w["lat"], w["lon"]] if isinstance(w, dict) else [w[0], w[1]] for w in raw]
                label, buffer_m = permit.route, corridor.buffer_m
            except (ValueError, KeyError, TypeError, IndexError):
                pts = None
        if not pts or len(pts) < 2:
            label, pts = ROUTES[n % len(ROUTES)]
            buffer_m = None
        comp = check_vehicle_compliance(db, v)
        fleet.append({
            "id": str(v.id), "plate": v.registration_number, "make": v.make, "model": v.model, "year": v.year,
            "colour": getattr(v, "color", None) or getattr(v, "colour", None), "owner": v.owner_name,
            "route": label, "path": _Path(pts), "pts": pts, "buffer_m": buffer_m,
            "psv": ({"permit": permit.permit_number, "operator": operators[permit.operator_id].name if permit.operator_id in operators else None,
                     "expires": permit.expiry_date.isoformat()} if permit else None),
            "issues": comp.issues, "checks": comp.checks, "blacklisted": bool(v.is_blacklisted),
            "speed": 38 + _h(v.registration_number, 45), "phase": _h(v.registration_number + "p", 1000) / 1000.0,
            "strays": bool(corridor) and n % 2 == 0,
        })
    return fleet


def _fleet(db: Session) -> list[dict]:
    now = time.time()
    if now - _cache["t"] > CACHE_S or not _cache["fleet"]:
        _cache["fleet"], _cache["t"] = _build_fleet(db), now
    return _cache["fleet"]


def _position(f: dict, t: float) -> dict:
    t = t * TIME_SCALE
    path: _Path = f["path"]
    v_ms = f["speed"] / 3.6
    run = path.length / v_ms                      # seconds to drive one way
    cycle = 2 * (run + DWELL_S)
    c = (t + f["phase"] * cycle) % cycle
    outbound = c < run + DWELL_S
    c = c if outbound else c - (run + DWELL_S)
    moving = c < run
    dist = min(c, run) * v_ms
    s = dist if outbound else path.length - dist
    lat, lon, brg = path.at(s)
    if not outbound:
        brg = (brg + 180) % 360
    off = 0.0
    if f["strays"] and moving:
        frac = dist / path.length
        if 0.45 < frac < 0.65:            # drifts off the licensed route for a stretch of every run
            off = math.sin(math.pi * (frac - 0.45) / 0.2) * max((f["buffer_m"] or 500) * 4, 2500)
            nx, ny = math.cos(math.radians(brg)), -math.sin(math.radians(brg))
            lat += (ny * off) / 111_320.0
            lon += (nx * off) / path.k
    speed = 0 if not moving else round(f["speed"] + 8 * math.sin(t / 9 + f["phase"] * 6.28))
    return {"lat": round(lat, 6), "lon": round(lon, 6), "heading": round(brg), "speed_kmh": speed, "moving": moving}


def snapshot(db: Session, now: float | None = None) -> list[dict]:
    t = time.time() if now is None else now
    out = []
    for f in _fleet(db):
        p = _position(f, t)
        off_route, distance = False, None
        if f["buffer_m"]:
            distance = round(distance_to_corridor_m(p["lat"], p["lon"], f["pts"]))
            off_route = distance > f["buffer_m"]
        problems = list(f["issues"])
        if off_route:
            problems.insert(0, f"Off its licensed route by {distance} m (allowed {f['buffer_m']} m)")
        state = "offroute" if off_route else ("flagged" if problems else "ok")
        out.append({
            "id": f["id"], "plate": f["plate"], "make": f["make"], "model": f["model"], "year": f["year"], "colour": f["colour"],
            "owner": f["owner"], "route": f["route"], "route_points": f["pts"], "buffer_m": f["buffer_m"], "psv": f["psv"], "state": state, "problems": problems,
            "checks": f["checks"], "blacklisted": f["blacklisted"], "distance_from_route_m": distance, "allowed_m": f["buffer_m"],
            "updated_at": t, **p,
        })
    return out
