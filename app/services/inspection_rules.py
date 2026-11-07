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


# Fault codes read from the vehicle's OBD-II port. Blocking codes fail the test; the rest are advisory.
# The table is a small illustrative set, not the full SAE J2012 list.
KNOWN_DTCS = {
    "P0300": "Random or multiple cylinder misfire", "P0301": "Cylinder 1 misfire", "P0302": "Cylinder 2 misfire",
    "P0303": "Cylinder 3 misfire", "P0304": "Cylinder 4 misfire", "P0420": "Catalytic converter efficiency below threshold",
    "P0430": "Catalytic converter efficiency below threshold (bank 2)", "P0171": "Fuel mixture too lean",
    "P0172": "Fuel mixture too rich", "P0442": "Small evaporative emission leak", "P0455": "Large evaporative emission leak",
    "P0500": "Vehicle speed sensor fault", "P0128": "Coolant temperature below thermostat range",
    "P0335": "Crankshaft position sensor fault", "C0035": "Front wheel speed sensor fault (ABS)",
    "C0040": "Right front wheel speed sensor fault (ABS)", "B0001": "Driver airbag circuit fault", "U0100": "Lost communication with engine control unit",
}
_SYSTEMS = {"P": "Engine and emissions", "C": "Chassis (brakes, steering, suspension)", "B": "Body (airbags, locks, climate)", "U": "Network"}


def _blocking(code: str) -> bool:
    return code.startswith(("P030", "P042", "C00", "B000", "B001"))


def decode_dtcs(codes: list[str] | None) -> list[dict]:
    out, seen = [], set()
    for raw in codes or []:
        code = str(raw).strip().upper()
        if not code or code in seen:
            continue
        if len(code) != 5 or code[0] not in _SYSTEMS or any(ch not in "0123456789ABCDEF" for ch in code[1:]):
            raise ValueError(f"{raw!r} is not a valid fault code (expected a letter P, C, B or U and four digits, such as P0420)")
        seen.add(code)
        out.append({"code": code, "system": _SYSTEMS[code[0]], "meaning": KNOWN_DTCS.get(code, "Not in the demo table: look it up for this make"),
                    "blocking": _blocking(code)})
    return out


def spec() -> dict:
    return {
        "checklist": [{"id": k, "label": v} for k, v in CHECKLIST],
        "readings": [{"id": k, "label": v[0], "unit": v[1], "min": v[2], "max": v[3]} for k, v in READINGS.items()],
    }


def evaluate(checklist: dict | None, readings: dict | None, obd_codes: list[str] | None = None) -> tuple[str | None, list[str]]:
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
    for d in decode_dtcs(obd_codes):
        if d["blocking"]:
            reasons.append(f"OBD-II fault {d['code']}: {d['meaning']}")
    if not checklist and not readings and not obd_codes:
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
