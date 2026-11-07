"""Place names for the BI screens: which road, which province, which season an offence belongs to."""

import math
import re

# Provincial capitals (approximate centre of each province, for nearest-centre grouping).
PROVINCES = {
    "Lusaka": (-15.42, 28.28), "Copperbelt": (-12.97, 28.64), "Central": (-14.45, 28.45), "Southern": (-16.8, 26.9),
    "Western": (-15.25, 23.13), "Eastern": (-13.63, 32.65), "Northern": (-10.2, 31.18), "Luapula": (-11.2, 28.9),
    "North-Western": (-12.18, 26.38), "Muchinga": (-11.2, 31.5),
}
_KEYWORDS = [
    ("Lusaka", ("lusaka", "chongwe", "kafue", "chilanga", "chirundu", "unza", "cairo road", "independence", "lumumba", "addis ababa", "mumbwa road")),
    ("Copperbelt", ("kitwe", "ndola", "chingola", "chililabombwe", "mufulira", "luanshya", "copperbelt")),
    ("Central", ("kabwe", "kapiri", "mkushi", "serenje")),
    ("Southern", ("livingstone", "maramba", "choma", "mazabuka", "monze")),
    ("Western", ("mongu", "kaoma", "senanga")),
    ("Eastern", ("chipata", "petauke", "katete")),
    ("Northern", ("kasama", "mbala", "mpika")),
]


def province_of(lat: float | None, lon: float | None, text: str | None = None) -> str:
    if text:
        low = text.lower()
        for name, words in _KEYWORDS:
            if any(w in low for w in words):
                return name
    if lat is not None and lon is not None:
        best = min(PROVINCES.items(), key=lambda kv: math.hypot(kv[1][0] - lat, (kv[1][1] - lon) * math.cos(math.radians(lat))))
        return best[0]
    return "Unknown"


def road_of(location: str | None) -> str:
    """'Great East Road, near UNZA' -> 'Great East Road'. Falls back to the whole text."""
    text = (location or "").strip()
    if not text:
        return "Unknown"
    head = re.split(r"[,(]", text)[0].strip()
    return head or text


SEASONS = {"Rainy season (Nov to Apr)": (11, 12, 1, 2, 3, 4), "Cold dry season (May to Aug)": (5, 6, 7, 8), "Hot dry season (Sep to Oct)": (9, 10)}


def season_of(month: int) -> str:
    for name, months in SEASONS.items():
        if month in months:
            return name
    return "Unknown"
