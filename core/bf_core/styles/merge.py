"""Unlinking a style that is deleted (ТЗ 7.16 «удаление используемого стиля оставляет слоям копию настроек»).

A port of BF `resolveTracks` / `unlinkStyle` (web/src/viz/downhole/styles.ts) for the one style: linked layers,
colourings and the template take its last body, then every reference to it is dropped. The interface draws
linked displays with the same rule, so the look does not change. Fixtures shared with vitest:
core/tests/data/styles/*.json.
"""

from __future__ import annotations

import copy
from typing import Any

#: What binds a layer to its data and its place in the display: never a part of a style.
BINDING = ("id", "table", "field", "style", "visible")
#: The fields a layer style starts from (BF applyLayerStyle).
LAYER_DEFAULTS: dict[str, Any] = {"type": "bars", "width": 90, "min": "", "max": "", "log": False, "boundaries": False}


def _refers(ref: Any, uid: str) -> bool:
    return isinstance(ref, dict) and ref.get("uid") == uid


def _linked(ref: Any, uid: str) -> bool:
    return _refers(ref, uid) and ref.get("linked") is True


def apply_layer_style(track: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"id": track.get("id"), "table": track.get("table"), "field": track.get("field")}
    out.update(LAYER_DEFAULTS)
    if "color" in track:
        out["color"] = track["color"]
    out.update({k: copy.deepcopy(v) for k, v in body.items() if k not in BINDING})
    if track.get("visible") is False:
        out["visible"] = False
    return out


def palette_fields(body: dict[str, Any]) -> dict[str, Any]:
    kind = body.get("kind")
    if kind == "classes":
        return {"colorBy": "classes", "classes": copy.deepcopy(body.get("classes", []))}
    if kind == "gradient":
        return {"colorBy": "scale", "gradient": copy.deepcopy(body.get("gradient", {}))}
    return {
        "catColors": copy.deepcopy(body.get("catColors", {})),
        "hidden": copy.deepcopy(body.get("hidden")),
        "catOrder": copy.deepcopy(body.get("catOrder")),
    }


def _apply_template(tracks: list[dict[str, Any]], body: dict[str, Any]) -> list[dict[str, Any]]:
    raw = body.get("tracks")
    src: list[Any] = raw if isinstance(raw, list) else []
    used: set[int] = set()
    out = []
    for t in tracks:
        field = str(t.get("field", "")).upper()
        i = next(
            (
                j
                for j, x in enumerate(src)
                if j not in used and isinstance(x, dict) and str(x.get("field", "")).upper() == field
            ),
            -1,
        )
        if i < 0:
            out.append(t)
            continue
        used.add(i)
        rest = {k: copy.deepcopy(v) for k, v in src[i].items() if k != "field"}
        out.append({**t, **rest, "id": t.get("id"), "table": t.get("table"), "field": t.get("field")})
    return out


def unlink(settings: dict[str, Any], uid: str, level: str, body: dict[str, Any]) -> dict[str, Any] | None:
    """The display settings without the style `uid` (its last body merged into the copies); None: not used."""
    raw = settings.get("tracks")
    tracks: list[Any] = raw if isinstance(raw, list) else []
    template = settings.get("template")
    if not _refers(template, uid) and not any(
        isinstance(t, dict) and (_refers(t.get("style"), uid) or _refers(t.get("palette"), uid)) for t in tracks
    ):
        return None
    out = copy.deepcopy(settings)
    new_tracks = [t for t in out.get("tracks", []) if isinstance(t, dict)] if tracks else []
    if _linked(template, uid) and level == "display":
        new_tracks = _apply_template(new_tracks, body)
    if _refers(template, uid):
        out.pop("template", None)
    result = []
    for t in new_tracks:
        if _linked(t.get("style"), uid) and level == "layer":
            t = apply_layer_style(t, body)
        if _linked(t.get("palette"), uid) and level == "palette":
            fields = palette_fields(body)
            # as the interface: the colouring replaces these keys, an absent one is removed (categories)
            t = {k: v for k, v in {**t, **fields}.items() if not (k in fields and fields[k] is None)}
        if _refers(t.get("style"), uid):
            t = {k: v for k, v in t.items() if k != "style"}
        if _refers(t.get("palette"), uid):
            t = {k: v for k, v in t.items() if k != "palette"}
        result.append(t)
    if tracks:
        out["tracks"] = result
    return out


def refs_of(settings: dict[str, Any], uid: str) -> tuple[bool, list[str]] | None:
    """(linked, fields) when a display uses the style — linked or as the source of a copy; None otherwise."""
    refs: list[Any] = []
    fields: list[str] = []
    if _refers(settings.get("template"), uid):
        refs.append(settings["template"])
    tracks = settings.get("tracks")
    for t in tracks if isinstance(tracks, list) else []:
        if not isinstance(t, dict):
            continue
        mine = [t[k] for k in ("style", "palette") if _refers(t.get(k), uid)]
        if mine:
            refs.extend(mine)
            f = t.get("field")
            if isinstance(f, str) and f not in fields:
                fields.append(f)
    if not refs:
        return None
    return any(r.get("linked") is True for r in refs), fields
