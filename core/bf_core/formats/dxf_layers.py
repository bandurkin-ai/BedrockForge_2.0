"""What `dxf_lines` reads, and how its entities become `lineset.LineSet` layers (ADR 0011 §1–2).

`dxf_lines` collects entities block by block and kind by kind (`Batch`); `build_layers` puts them back into the
order of the file and splits them by DXF layer:
  * layers in the order of their first entity, entities and vertices in the order of the file; `seq` numbers the
    vertices of the file over all layers (a vertex added to close a line has the `seq` of the first one);
  * a layer whose entities all have one vertex (POINT, TEXT, MTEXT) is a layer of points, any other one is a
    layer of lines, where such an entity is a line of one vertex;
  * closed = flagged closed in the file, or the last vertex repeats the first (`lineset.detect_closed`); a flagged
    line gets the repeat of its first vertex (`lineset.close`);
  * `text`, `height`, `rotation` are attributes of the layers that hold texts; other features have them empty;
  * colour = that of the first entity of the layer (`color_of`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from bf_core.formats.lineset import LineSet, close, detect_closed

if TYPE_CHECKING:
    from bf_core.formats.meshset import MeshSet


@dataclass
class DxfHeader:
    version: str | None  # $ACADVER
    codepage: str | None  # $DWGCODEPAGE
    layers: dict[str, str | None] = field(default_factory=dict)  # the LAYER table: name → colour


@dataclass
class DxfRead:
    format: str  # "dxf"
    layers: list[LineSet]
    header: DxfHeader
    encoding: str
    records: int  # entities loaded: lines, points and texts
    warnings: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)  # entities that are not loaded, by DXF type
    meshes: int = 0  # mesh entities in the file: POLYFACE, polygon mesh, 3DFACE, MESH
    wireframes: list[MeshSet] = field(default_factory=list)  # the meshes of every layer (`dxf_meshes`)
    recovered: bool = False

    @property
    def line_count(self) -> int:
        return sum(layer.line_count for layer in self.layers)

    @property
    def point_count(self) -> int:
        return sum(layer.point_count for layer in self.layers)


def ranges(starts: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Concatenation of arange(s, s + c) for every (s, c)."""
    total = int(counts.sum())
    shift = np.repeat(starts - (np.cumsum(counts) - counts), counts)
    return np.asarray(shift + np.arange(total, dtype=np.int64))


def _hex(rgb: int) -> str:
    return f"#{rgb & 0xFFFFFF:06x}"


def color_of(aci: int, true: int, layer: tuple[int, int] | None) -> str | None:
    """Colour of an entity: true colour, its ACI, else by layer; 7 (white or black) is no colour.
    `layer` — (ACI, true colour) of the LAYER table: a negative ACI is a layer switched off, 0 — no colour."""
    if true >= 0:
        return _hex(true)
    if aci in (0, 256, -1):  # BYBLOCK, BYLAYER, not given
        if layer is None:
            return None
        if layer[1] >= 0:
            return _hex(layer[1])
        aci = abs(layer[0])
    if not 1 <= aci <= 255 or aci == 7:
        return None
    from ezdxf import colors

    r, g, b = colors.aci2rgb(aci)
    return f"#{r:02x}{g:02x}{b:02x}"


@dataclass
class Batch:
    ord: np.ndarray  # the number of the entity's record in the file
    layer: np.ndarray
    counts: np.ndarray
    closed: np.ndarray
    xyz: np.ndarray
    text: list[str] | None = None
    height: np.ndarray | None = None
    rotation: np.ndarray | None = None


def build_layers(
    bs: list[Batch],
    names: dict[int, str],
    tables: dict[int, tuple[int, int] | None],
    colors: dict[int, tuple[int, int, int]],
) -> list[LineSet]:
    """Batches of entities → layers. `names` — layer id → name, `tables` — its (ACI, true colour) in the LAYER
    table, `colors` — (record, ACI, true colour) of its first entity. The batches are emptied."""
    if not bs:
        return []
    ords, layer, counts, closed = (
        np.concatenate([getattr(x, k) for x in bs]) for k in ("ord", "layer", "counts", "closed")
    )
    xyz = np.concatenate([x.xyz for x in bs])
    for x in bs:  # the vertices now live in one array: the batches give their memory back
        x.xyz = xyz[:0]
    has_text = any(x.text is not None for x in bs)
    texts: list[str] = []
    ref = np.full(len(ords), -1, dtype=np.int64)  # the text of every feature
    height = rotation = np.full(len(ords) if has_text else 0, np.nan)
    if has_text:
        rotation = height.copy()
        at = 0
        for x in bs:
            if x.text is not None and x.height is not None and x.rotation is not None:
                ref[at : at + len(x.ord)] = np.arange(len(texts), len(texts) + len(x.ord))
                height[at : at + len(x.ord)], rotation[at : at + len(x.ord)] = x.height, x.rotation
                texts.extend(x.text)
            at += len(x.ord)
    starts = np.cumsum(counts) - counts
    if len(ords) > 1 and (np.diff(ords) < 0).any():  # entities of different kinds were collected apart
        order = np.argsort(ords, kind="stable")
        xyz = xyz[ranges(starts[order], counts[order])]
        ords, layer, counts, closed, ref = ords[order], layer[order], counts[order], closed[order], ref[order]
        if has_text:
            height, rotation = height[order], rotation[order]
        starts = np.cumsum(counts) - counts
    found, first = np.unique(layer, return_index=True)
    by_layer = np.argsort(layer, kind="stable")
    bounds = np.append(np.searchsorted(layer[by_layer], found), len(layer))
    out: list[LineSet] = []
    for k in np.argsort(first, kind="stable").tolist():
        f = by_layer[bounds[k] : bounds[k + 1]]
        lay = int(found[k])
        c = counts[f]
        vertex = ranges(starts[f], c)
        pts, seq = (xyz, vertex) if len(found) == 1 else (xyz[vertex], vertex)
        attrs = None
        if has_text and (ref[f] >= 0).any():
            attrs = {
                "text": [texts[i] if i >= 0 else None for i in ref[f].tolist()],
                "height": height[f],
                "rotation": rotation[f],
            }
        _, aci, true = colors[lay]
        color = color_of(aci, true, tables[lay])
        if (c == 1).all():
            out.append(LineSet(names[lay], pts, None, None, seq, attrs, None, color))
            continue
        offsets = np.concatenate([[0], np.cumsum(c)]).astype(np.int64)
        pts, offsets, flags, take = close(pts, offsets, closed[f] | (detect_closed(pts, offsets) != 0))
        out.append(LineSet(names[lay], pts, offsets, flags, seq if take is None else seq[take], attrs, None, color))
    return out
