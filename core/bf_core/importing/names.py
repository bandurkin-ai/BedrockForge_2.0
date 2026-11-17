"""Matching column names of source files to the fields BedrockForge needs.

Drillhole and block model files come from many systems, each with its own naming:
Micromine (field names of at most 10 characters, so "DrillholeID" is stored as "DrillholeI"),
Datamine (8 characters: BHID, XCOLLAR, AT, BRG, XC, XINC), Surpac, Vulcan, Leapfrog, acQuire,
GBIS, Excel tables with units in the header ("Глубина, м", "Depth_m", "Dip (deg)").

Aliases are tried in priority order; each is compared in three ways:
  1. the canonical form: upper case, units and separators dropped ("Depth_m" → "DEPTH");
  2. the compact form: also without "_" ("HOLE_ID" = "HOLEID" = "Hole ID");
  3. a truncated name: a source name of 6+ characters that is the start of an alias
     ("DRILLHOLEI" → "DRILLHOLEID", "GRIDBEARIN" → "GRIDBEARING").
The suggestion is only a start: the user checks and corrects it in the import wizard.
"""

from __future__ import annotations

import re

# Units and words that only say how a value is measured: "Depth_m", "Dip_deg", "Au_ppm", "X (м)".
_UNIT_WORDS = {
    "M", "METRE", "METRES", "METER", "METERS", "МЕТР", "МЕТРЫ", "М", "FT", "FEET",
    "DEG", "DEGREE", "DEGREES", "ГРАД", "ГРАДУС", "ГРАДУСЫ",
    "PCT", "PERCENT", "PPM", "PPB", "GT", "G_T", "GPT", "OZT", "ПРОЦ",
}
_SPLIT = re.compile(r"[^0-9A-ZА-ЯЁ№]+")
_UNIT_TAIL = re.compile(r"[,(\[].*$")


def canon(name: str) -> str:
    """'Depth, m' -> 'DEPTH'; 'Dip_deg' -> 'DIP'; 'Hole ID' -> 'HOLE_ID'; 'Х' (Cyrillic) -> 'X'."""
    text = _UNIT_TAIL.sub("", str(name)).strip() or str(name)
    text = text.upper().replace("Ё", "Е")
    words = [w for w in _SPLIT.split(text) if w]
    while len(words) > 1 and words[-1] in _UNIT_WORDS:
        words.pop()
    out = "_".join(words)
    # Cyrillic letters that look like Latin ones in one-letter coordinate names.
    return {"Х": "X", "У": "Y"}.get(out, out)


def compact(name: str) -> str:
    return canon(name).replace("_", "")


def match_column(columns: list[str], aliases: tuple[str, ...] | list[str], used: set[str] = frozenset()) -> str | None:
    """The column for the first alias found (aliases in priority order), or None."""
    free = [c for c in columns if c not in used]
    by_canon: dict[str, str] = {}
    by_compact: dict[str, str] = {}
    for col in free:
        by_canon.setdefault(canon(col), col)
        by_compact.setdefault(compact(col), col)
    # The alias order is the priority: a grid azimuth cut to 10 characters ("GridBearin") beats a
    # magnetic azimuth named in full ("AzimuthMag").
    for alias in aliases:
        a = canon(alias)
        if a in by_canon:
            return by_canon[a]
        a = compact(alias)
        if a in by_compact:
            return by_compact[a]
        if len(a) > 6:  # a truncated name: the source name is the start of a long alias
            for key, col in by_compact.items():
                if 6 <= len(key) < len(a) and a.startswith(key):
                    return col
    return None
