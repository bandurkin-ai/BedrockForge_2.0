"""Names of the sections of a series (ТЗ 8.1.2; ADR 0016 §2): a template in a safe subset of the Python format.

Fields: `{k}` — the number of the section from 1, `{s}` — s_k, `{X}` `{Y}` `{Z}` — its origin O_k; a field may
carry the format of a number in the standard mini-language (`{Y:.0f}`, `{k:03d}`, `{s:+.1f}`, width ≤ 50,
precision ≤ 20). The template is split by `string.Formatter().parse` and every field is formatted by
`format(value, spec)` — never `str.format`, which would open the attributes and items of the objects. Refused
(SeriesError, 422 `bad_series`, field `template`): any other field, an attribute or an index (`{X.__class__}`,
`{X[0]}`), a positional or automatic field (`{0}`, `{}`), a conversion (`{k!r}`), a field inside a format
(`{s:{k}}`), a format that does not fit the number, a lone brace, control characters, more than 200 characters.

Defaults (8.1.2): A = 90° or 270° — `{Y:.0f}N`, A = 0° or 180° — `{X:.0f}E`, a plan — `{Z:.0f}`, otherwise (a
polyline too) `{s:+.0f}`. When the default gives the same name twice (Δ under 1 m), its precision grows to
.1f … .3f, then `{s:+.Nf}` is taken; a template of the user that does is refused («добавьте {k}»).

    check_template(template)               → the parts of the template (SeriesError if it is not one)
    default_template(geom)                 → the default of 8.1.2 for this series
    section_names(meta | geom, template)   → the K names
    series_table(meta)                     → {count, sections: [{k, name, s, origin, towards, away}]} (+ segments)
"""

from __future__ import annotations

import re
import string
import unicodedata
from collections.abc import Mapping
from typing import Any

import numpy as np

from .geometry import SeriesError, SeriesGeometry, series_geometry
from .polyline import PolylineSection

FIELDS = ("k", "s", "X", "Y", "Z")
MAX_TEMPLATE = 200
MAX_WIDTH, MAX_PRECISION = 50, 20
_SPEC = re.compile(
    r"(?:(?P<fill>[^{}])?[<>=^])?[-+ ]?z?#?0?(?P<width>\d+)?[,_]?(?:\.(?P<precision>\d+))?[deEfFgGn%xXob]?"
)
_HINT = "Допустимы поля {k}, {s}, {X}, {Y}, {Z} и формат числа, например «{Y:.0f}N» или «Р-{k:02d}»."
_SAMPLE = {"k": 12, "s": -1234.5, "X": 512345.678, "Y": 7012345.123, "Z": -0.5}

Geometry = SeriesGeometry | PolylineSection
Parts = list[tuple[str, str | None, str]]


def _bad(message: str, hint: str = _HINT) -> SeriesError:
    return SeriesError(message, hint, field="template")


def check_template(template: Any) -> Parts:
    """The parts (literal text, field or None, format) of a valid template; SeriesError otherwise."""
    if not isinstance(template, str) or not template.strip():
        raise _bad("Шаблон имён пуст.")
    if len(template) > MAX_TEMPLATE:
        raise _bad(f"Шаблон имён длиннее {MAX_TEMPLATE} символов.", "Сократите шаблон.")
    if any(unicodedata.category(ch) == "Cc" for ch in template):
        raise _bad("В шаблоне имён есть управляющие символы.", "Уберите переводы строк и табуляции.")
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError:
        raise _bad(
            "В шаблоне имён непарная фигурная скобка.", "Скобку в тексте пишите дважды: «{{» или «}}»."
        ) from None
    parts: Parts = []
    for literal, name, raw, conversion in parsed:
        spec = raw or ""
        if name is None:
            parts.append((literal, None, ""))
            continue
        shown = "{" + name[:20] + "}"
        if conversion is not None:
            raise _bad(f"Шаблон имён: преобразование «!{conversion}» в поле {shown} не допускается.")
        if name not in FIELDS:
            raise _bad(f"Шаблон имён: неизвестное поле {shown}.")
        m = _SPEC.fullmatch(spec)
        if m is None or int(m["width"] or 0) > MAX_WIDTH or int(m["precision"] or 0) > MAX_PRECISION:
            raise _bad(f"Шаблон имён: неверный формат числа «{spec[:20]}» в поле {{{name}}}.")
        try:
            format(_SAMPLE[name], spec)
        except (ValueError, TypeError):
            raise _bad(f"Шаблон имён: формат «{spec[:20]}» не подходит к полю {{{name}}}.") from None
        parts.append((literal, name, spec))
    return parts


def _values(geom: Geometry) -> tuple[np.ndarray, np.ndarray]:
    return geom.offs + 0.0, geom.origins() + 0.0


def _render(parts: Parts, geom: Geometry) -> list[str]:
    offs, origins = _values(geom)
    out = []
    for k in range(geom.count):
        value = {"k": k + 1, "s": float(offs[k]), "X": float(origins[k, 0]), "Y": float(origins[k, 1]),
                 "Z": float(origins[k, 2])}  # fmt: skip
        text = []
        for literal, name, spec in parts:
            text.append(literal)
            if name is not None:
                try:
                    text.append(format(value[name], spec))
                except (ValueError, TypeError):
                    raise _bad(f"Шаблон имён: формат «{spec[:20]}» не подходит к полю {{{name}}}.") from None
        out.append("".join(text))
    return out


def _repeat(names: list[str]) -> tuple[int, int] | None:
    seen: dict[str, int] = {}
    for i, name in enumerate(names):
        if name in seen:
            return seen[name], i
        seen[name] = i
    return None


def _default_bases(geom: Geometry) -> list[tuple[str, str, str]]:
    """(field, sign, suffix) of the default templates, the first one of 8.1.2 first."""
    by_s = ("s", "+", "")
    if isinstance(geom, PolylineSection):
        return [by_s]
    if geom.kind == "plan":
        return [("Z", "", ""), by_s]
    if geom.az in (90.0, 270.0):
        return [("Y", "", "N"), by_s]
    if geom.az in (0.0, 180.0):
        return [("X", "", "E"), by_s]
    return [by_s]


def _default(geom: Geometry) -> tuple[str, list[str]]:
    first: tuple[str, list[str]] | None = None
    for name, sign, suffix in _default_bases(geom):
        for digits in range(4):
            template = "{" + f"{name}:{sign}.{digits}f" + "}" + suffix
            names = _render(check_template(template), geom)
            first = first or (template, names)
            if _repeat(names) is None:
                return template, names
    assert first is not None
    return first  # not reached: s grows by Δ ≥ 0.01


def default_template(geom: Geometry) -> str:
    """The default of 8.1.2; its precision grows (.0f … .3f, then by s) until the names differ."""
    return _default(geom)[0]


def _geometry(source: Mapping[str, Any] | Geometry) -> tuple[Geometry, Any]:
    if isinstance(source, SeriesGeometry | PolylineSection):
        return source, None
    return series_geometry(source), source.get("template")


def section_names(source: Mapping[str, Any] | Geometry, template: str | None = None) -> list[str]:
    """The K names of the sections: by `template` (for the parameters of a series — by its own `template`),
    by the default when it is None. SeriesError: a bad template or one that gives a name twice."""
    geom, own = _geometry(source)
    template = own if template is None and own not in (None, "") else template
    if template is None or template == "":
        return _default(geom)[1]
    names = _render(check_template(template), geom)
    same = _repeat(names)
    if same is not None:
        a, b = same
        raise SeriesError(
            f"Имена разрезов {a + 1} и {b + 1} совпадают: «{names[a][:40]}».",
            "Добавьте в шаблон номер разреза {k} или больше знаков после запятой, например «{Y:.1f}N».",
            field="template",
        )
    return names


def series_table(meta: Mapping[str, Any]) -> dict[str, Any]:
    """The table of the sections of a series (ADR 0016 §3, `GET /section-series/{id}/table` without its
    version): number k from 0, name, s_k, origin O_k, the window (a polyline — its corridor); `segments` for a
    polyline."""
    geom, _ = _geometry(meta)
    names = section_names(meta)
    offs, origins = _values(geom)
    rows = [
        {
            "k": k,
            "name": names[k],
            "s": float(offs[k]),
            "origin": [float(x) for x in origins[k]],
            "towards": float(geom.towards[k]),
            "away": float(geom.away[k]),
        }
        for k in range(geom.count)
    ]
    out: dict[str, Any] = {"count": geom.count, "sections": rows}
    if isinstance(geom, PolylineSection):
        out["segments"] = geom.segments
    return out
