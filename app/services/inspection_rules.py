"""The roadworthiness test: a checklist plus instrument readings decide the result.

Limits are illustrative, set for the demo; RTSA's own test standard replaces them.
"""

import json

CHECKLIST = [
    ("brakes", "Brakes"), ("steering", "Steering and suspension"), ("tyres", "Tyres"),
    ("lights", "Lights and indicators"), ("wipers", "Windscreen and wipers"),
    ("seat_belts", "Seat belts"), ("horn_mirrors", "Horn and mirrors"),
    ("exhaust", "Exhaust and emissions"), ("body", "Body and chassis"),
    ("safety_kit", "Warning triangle and fire extinguisher"),
]

# reading key -> (label, unit, minimum, maximum)
READINGS = {
    "brake_efficiency_pct": ("Brake efficiency", "%", 50, None),
    "tyre_tread_mm": ("Tyre tread depth", "mm", 1.6, None),
    "emission_co_pct": ("Exhaust carbon monoxide", "%", None, 4.5),
}


def spec() -> dict:
    return {
        "checklist": [{"id": k, "label": v} for k, v in CHECKLIST],
        "readings": [{"id": k, "label": v[0], "unit": v[1], "min": v[2], "max": v[3]} for k, v in READINGS.items()],
    }


def evaluate(checklist: dict | None, readings: dict | None) -> tuple[str | None, list[str]]:
    """Return ("passed"|"failed"|None, reasons). None when nothing was recorded."""
    reasons: list[str] = []
    labels = dict(CHECKLIST)
    for key, val in (checklist or {}).items():
        if key not in labels:
            raise ValueError(f"Unknown checklist item: {key}")
        if val not in ("pass", "fail", "na"):
            raise ValueError(f"{labels[key]} must be pass, fail or na")
        if val == "fail":
            reasons.append(f"{labels[key]} failed")
    for key, val in (readings or {}).items():
        if key not in READINGS:
            raise ValueError(f"Unknown reading: {key}")
        label, unit, lo, hi = READINGS[key]
        if lo is not None and val < lo:
            reasons.append(f"{label} {val}{unit} is below the minimum {lo}{unit}")
        if hi is not None and val > hi:
            reasons.append(f"{label} {val}{unit} is above the maximum {hi}{unit}")
    if not checklist and not readings:
        return None, reasons
    done = len(checklist or {})
    if done and done < len(CHECKLIST) and not reasons:
        raise ValueError(f"Complete all {len(CHECKLIST)} checklist items before passing the vehicle")
    return ("failed" if reasons else "passed"), reasons


def dump(d: dict | None) -> str | None:
    return json.dumps(d) if d else None


def load(s: str | None) -> dict:
    try:
        return json.loads(s) if s else {}
    except ValueError:
        return {}
