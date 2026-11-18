"""DXF lines, points and texts → `lineset.LineSet` layers (ТЗ 7.18, 9.5, 9.12; ADR 0011 §2).

`read_dxf(path, progress)` → `DxfRead`: one `LineSet` per DXF layer, in the order of the file.
  * LINE, LWPOLYLINE, POLYLINE with its VERTEX records (2D and 3D), ARC, CIRCLE, ELLIPSE, SPLINE are lines; curves
    and bulges become polylines within `tolerance` (0.1 m) of the curve (`dxf_curves`). POINT, TEXT and MTEXT are
    lines of one vertex; a layer that holds nothing else is a layer of points. A text carries the attributes
    `text`, `height`, `rotation` (degrees, counter-clockwise); other features of the layer have them empty.
  * Coordinates of LWPOLYLINE, 2D POLYLINE, ARC, CIRCLE and TEXT are in the entity's own system (OCS): they are
    turned into the world system by the extrusion normal (the arbitrary axis algorithm, `ocs_to_wcs`).
  * A closed flag, a circle or a whole ellipse gives a closed line: the first vertex is repeated at the end. A line
    whose ends coincide is closed as well (`lineset.detect_closed`). `seq` numbers the vertices in file order.
  * A group the format lets a program leave out (Z of a 2D point, elevation of an LWPOLYLINE) has its DXF default,
    0: the value is defined, not missing. A coordinate that is not a number is empty: the vertex is dropped when it
    is X or Y, Z stays NaN.
  * The colour of a layer is the colour of its first entity: true colour, else ACI, else that of the LAYER table.
  * Not loaded and counted in `skipped`: block references (INSERT), hatches, dimensions and so on, entities of
    paper space. Meshes (POLYFACE, polygon mesh, 3DFACE, MESH) are wireframes, read by the Э8 reader: `meshes`.
The file goes through `dxf_stream` in blocks: memory is the result plus one block. `Limits` stop "bombs".
`recover`: None — what the plain reading does not take is mended, and the result says so (`recovered`, a warning):
a file cut short is read up to its last whole record (`DxfStream(salvage=True)`: ezdxf does not read such files);
a binary DXF is loaded by ezdxf and a text file with a broken structure by `ezdxf.recover`, saved as a clean ASCII
DXF into a temporary folder and read from there. False — such a file is an error; True — always through ezdxf.
Error codes: those of `dxf_stream` and too_large.

Ported from BF `formats/lines.py::read_dxf_lines`. Differences: own streaming reader instead of an ezdxf document
(memory), texts, colour of the LAYER table, paper space left out, limits, progress and cancellation.
"""

from __future__ import annotations

import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.formats import dxf_curves as curves
from bf_core.formats import dxf_stream as ds
from bf_core.formats.dxf_layers import Batch, DxfHeader, DxfRead, build_layers, color_of, ranges
from bf_core.formats.dxf_stream import Block, DxfStream, fail, n_text
from bf_core.formats.errors import FormatError
from bf_core.formats.table import Progress, no_progress

_READ = (
    ds.K_LINE,
    ds.K_POINT,
    ds.K_LWPOLYLINE,
    ds.K_POLYLINE,
    ds.K_VERTEX,
    ds.K_SEQEND,
    ds.K_TEXT,
    ds.K_MTEXT,
    ds.K_ARC,
    ds.K_CIRCLE,
    ds.K_ELLIPSE,
    ds.K_SPLINE,
)
_Z = np.array([0.0, 0.0, 1.0])


@dataclass(frozen=True)
class Limits:
    vertices: int = 20_000_000  # of all layers, curves flattened
    texts: int = 2_000_000
    record_bytes: int = ds.MAX_RECORD_BYTES
    recover_bytes: int = 64 << 20  # ezdxf holds the whole drawing: measured 8 MB of memory and 0.6 s per MB of file


def _whole(values: np.ndarray, missing: float = -1.0) -> np.ndarray:
    """Values that stand for flags and colours: what is not a number of a sane size is `missing`."""
    with np.errstate(invalid="ignore"):
        return np.where(np.isfinite(values) & (np.abs(values) < 2.0**31), values, missing)


def ocs_axes(normal: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Axes X, Y, Z of the object coordinate systems with the extrusion normals (rows): the arbitrary axis
    algorithm of the DXF reference."""
    n = np.asarray(normal, dtype=np.float64).reshape(-1, 3)
    az = n / np.linalg.norm(n, axis=1)[:, None]
    near_z = (np.abs(az[:, 0]) < 1.0 / 64.0) & (np.abs(az[:, 1]) < 1.0 / 64.0)
    ax = np.where(near_z[:, None], np.cross([0.0, 1.0, 0.0], az), np.cross(_Z, az))
    ax /= np.linalg.norm(ax, axis=1)[:, None]
    ay = np.cross(az, ax)
    ay /= np.linalg.norm(ay, axis=1)[:, None]
    return ax, ay, az


def ocs_to_wcs(xyz: Any, normal: Any) -> np.ndarray:
    """Points of object coordinate systems → world; `normal` — one for all or one per point."""
    p = np.asarray(xyz, dtype=np.float64).reshape(-1, 3)
    ax, ay, az = ocs_axes(normal)
    return np.asarray(p[:, :1] * ax + p[:, 1:2] * ay + p[:, 2:3] * az)


def _ocs(xyz: np.ndarray, normals: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """`xyz` of entities (`counts` vertices each) with their normals; the default normal changes nothing."""
    with np.errstate(invalid="ignore"):
        length = np.linalg.norm(normals, axis=1)
    turn = (
        np.isfinite(length) & (length > 0.0) & ~((normals[:, 0] == 0.0) & (normals[:, 1] == 0.0) & (normals[:, 2] > 0))
    )
    if not turn.any():
        return xyz
    pick = np.repeat(turn, counts)
    out = xyz.copy()
    out[pick] = ocs_to_wcs(xyz[pick], np.repeat(normals, counts, axis=0)[pick])
    return out


class _Reader:
    def __init__(self, stream: DxfStream, tol: float, limits: Limits) -> None:
        self.s, self.tol, self.lim = stream, tol, limits
        self.names: dict[bytes, int] = {}  # layer name as in the file → id
        self.table: dict[bytes, tuple[int, int]] = {}  # the LAYER table: (ACI, true colour)
        self.color: dict[int, tuple[int, int, int]] = {}  # layer id → (record, ACI, true colour) of its first entity
        self.batches: list[Batch] = []
        self.vertices = self.texts = self.bad = self.dropped = self.no_z = self.meshes = 0
        self.skipped: Counter[bytes] = Counter()
        self.open: tuple[np.ndarray, list[np.ndarray]] | None = None  # a POLYLINE whose vertices go on

    def room(self) -> int:
        return self.lim.vertices - self.vertices

    # --- one block -----------------------------------------------------------------------------------

    def feed(self, b: Block) -> None:
        kind = b.kind
        tab = np.flatnonzero((kind == ds.K_LAYER) & (b.section == ds.S_TABLES))
        aci = _whole(b.values(b.col(62)[tab], 0.0), 0.0).astype(np.int64)  # negative: the layer is off; 0 — none
        true = _whole(b.values(b.col(420)[tab], -1.0)).astype(np.int64)
        for name, a, t in zip(b.col(2)[tab].tolist(), aci.tolist(), true.tolist(), strict=True):
            if name >= 0:
                self.table[b.value(name).strip()] = (a, t)
        ent = (b.section == ds.S_ENTITIES) & (kind != ds.K_SECTION)
        model = ent & ~(b.values(b.col(67), 0.0) == 1.0)
        self._polylines(b, ent, model)
        for k, handler in (
            (ds.K_LINE, self._lines),
            (ds.K_POINT, self._points),
            (ds.K_LWPOLYLINE, self._lwpolylines),
            (ds.K_TEXT, self._texts),
            (ds.K_MTEXT, self._mtexts),
            (ds.K_ARC, self._arcs),
            (ds.K_CIRCLE, self._arcs),
            (ds.K_ELLIPSE, self._ellipses),
            (ds.K_SPLINE, self._splines),
        ):
            sel = np.flatnonzero(model & (kind == k))
            if len(sel):
                handler(b, sel)
        self.meshes += int((model & ((kind == ds.K_3DFACE) | (kind == ds.K_MESH))).sum())
        paper = ent & ~model & (kind != ds.K_VERTEX) & (kind != ds.K_SEQEND)
        self.skipped[b"PAPERSPACE"] += int(paper.sum())
        other = np.flatnonzero(model & ~np.isin(kind, (*_READ, ds.K_3DFACE, ds.K_MESH)))
        self.skipped.update(b.value(p) for p in b.z[other].tolist())

    def _common(self, b: Block, sel: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """(record numbers, layer ids, ACI, true colour) of the entities `sel`."""
        layer = b.intern(b.col(8)[sel], self.names, b"0")
        return b.first + sel, layer, _whole(b.values(b.col(62)[sel], -1.0)), _whole(b.values(b.col(420)[sel], -1.0))

    def _point(
        self, b: Block, sel: np.ndarray, codes: tuple[int, int, int] = (10, 20, 30)
    ) -> tuple[np.ndarray, np.ndarray]:
        """(xyz, the entity has X and Y) for a point of the entities; Z left out is 0."""
        px, py, pz = (b.col(c)[sel] for c in codes)
        xyz = np.column_stack([b.values(px), b.values(py), b.values(pz, 0.0)])
        return xyz, (px >= 0) & (py >= 0)

    def _normals(self, b: Block, sel: np.ndarray) -> np.ndarray:
        return np.column_stack([b.values(b.col(c)[sel], d) for c, d in ((210, 0.0), (220, 0.0), (230, 1.0))])

    def _emit(self, b: Block, sel: np.ndarray, counts: Any, closed: Any, xyz: np.ndarray, **text: Any) -> None:
        self._add(
            *self._common(b, sel), np.asarray(counts, dtype=np.int64), np.asarray(closed, dtype=bool), xyz, **text
        )

    def _add(
        self,
        ords: np.ndarray,
        layer: np.ndarray,
        aci: np.ndarray,
        true: np.ndarray,
        counts: np.ndarray,
        closed: np.ndarray,
        xyz: np.ndarray,
        text: list[str] | None = None,
        height: np.ndarray | None = None,
        rotation: np.ndarray | None = None,
    ) -> None:
        """Entities with their vertices into the result: vertices without X or Y are dropped, a Z that is not a
        number is empty, entities left without vertices are dropped."""
        if len(xyz) > self.room():
            raise curves.too_many(self.lim.vertices)
        z_bad = ~np.isfinite(xyz[:, 2])
        if z_bad.any():
            self.no_z += int(z_bad.sum())
            xyz[z_bad, 2] = np.nan
        ok = np.isfinite(xyz[:, :2]).all(axis=1)
        if not ok.all():
            self.dropped += int((~ok).sum())
            counts = np.bincount(np.repeat(np.arange(len(counts)), counts)[ok], minlength=len(counts))
            xyz = xyz[ok]
        keep = counts > 0
        if not keep.all():
            ords, layer, aci, true, counts, closed = (v[keep] for v in (ords, layer, aci, true, counts, closed))
            if text is not None and height is not None and rotation is not None:
                text, height, rotation = (
                    [t for t, k in zip(text, keep.tolist(), strict=True) if k],
                    height[keep],
                    rotation[keep],
                )
        if len(counts) == 0:
            return
        found, first = np.unique(layer, return_index=True)
        for lay, i in zip(found.tolist(), first.tolist(), strict=True):
            if lay not in self.color or int(ords[i]) < self.color[lay][0]:
                self.color[lay] = (int(ords[i]), int(aci[i]), int(true[i]))
        self.vertices += len(xyz)
        self.batches.append(Batch(ords.astype(np.int64), layer, counts, closed, xyz, text, height, rotation))

    # --- entities ------------------------------------------------------------------------------------

    def _lines(self, b: Block, sel: np.ndarray) -> None:
        p1, ok1 = self._point(b, sel)
        p2, ok2 = self._point(b, sel, (11, 21, 31))
        ok = ok1 & ok2
        self.bad += int((~ok).sum())
        xyz = np.empty((2 * int(ok.sum()), 3))
        xyz[0::2], xyz[1::2] = p1[ok], p2[ok]
        self._emit(b, sel[ok], np.full(len(xyz) // 2, 2), np.zeros(len(xyz) // 2), xyz)

    def _points(self, b: Block, sel: np.ndarray) -> None:
        xyz, ok = self._point(b, sel)
        self.bad += int((~ok).sum())
        self._emit(b, sel[ok], np.ones(int(ok.sum())), np.zeros(int(ok.sum())), xyz[ok])

    def _put_texts(
        self, b: Block, sel: np.ndarray, xyz: np.ndarray, ok: np.ndarray, text: list[str], rotation: np.ndarray
    ) -> None:
        self.bad += int((~ok).sum())
        self.texts += int(ok.sum())
        if self.texts > self.lim.texts:
            raise fail(
                "too_large",
                f"В файле больше {n_text(self.lim.texts)} текстов: он слишком велик.",
                "Удалите лишние подписи в исходной программе или разделите чертёж на части.",
            )
        height = b.values(b.col(40)[sel])
        kept = [t for t, k in zip(text, ok.tolist(), strict=True) if k]
        self._emit(
            b,
            sel[ok],
            np.ones(int(ok.sum())),
            np.zeros(int(ok.sum())),
            xyz[ok],
            text=kept,
            height=height[ok],
            rotation=rotation[ok],
        )

    def _texts(self, b: Block, sel: np.ndarray) -> None:
        from ezdxf.tools.text import plain_text

        xyz, ok = self._point(b, sel)
        second, ok2 = self._point(b, sel, (11, 21, 31))
        h, v = b.values(b.col(72)[sel], 0.0), b.values(b.col(73)[sel], 0.0)
        aligned = ok2 & ((h != 0) | (v != 0)) & (h != 3) & (h != 5)  # the alignment point is where the text is put
        xyz = _ocs(np.where(aligned[:, None], second, xyz), self._normals(b, sel), np.ones(len(sel), dtype=np.int64))
        text = [plain_text(self.s.text(b.value(p))) if p >= 0 else "" for p in b.col(1)[sel].tolist()]
        self._put_texts(b, sel, xyz, ok | aligned, text, b.values(b.col(50)[sel], 0.0))

    def _mtexts(self, b: Block, sel: np.ndarray) -> None:
        from ezdxf.tools.text import plain_mtext

        xyz, ok = self._point(b, sel)
        mask = np.zeros(len(b), dtype=bool)
        mask[sel] = True
        parts: dict[int, list[bytes]] = {}
        chunks = np.sort(np.concatenate([b.pairs(3, mask), b.pairs(1, mask)]))
        for p, r in zip(chunks.tolist(), b.rec[chunks].tolist(), strict=True):
            parts.setdefault(r, []).append(b.value(p))
        text = [str(plain_mtext(self.s.text(b"".join(parts.get(r, ()))))) for r in sel.tolist()]
        dx, dy = b.col(11)[sel], b.col(21)[sel]
        along = np.degrees(np.arctan2(b.values(dy, 0.0), b.values(dx, 1.0)))  # the direction of the text line wins
        rotation = np.where((dx >= 0) & (dy >= 0), along, b.values(b.col(50)[sel], 0.0))
        self._put_texts(b, sel, xyz, ok, text, rotation)

    def _arcs(self, b: Block, sel: np.ndarray) -> None:
        c, ok = self._point(b, sel)
        r = b.values(b.col(40)[sel])
        circle = b.kind[sel[0]] == ds.K_CIRCLE
        a0, a1 = b.values(b.col(50)[sel], 0.0), b.values(b.col(51)[sel], 0.0)
        with np.errstate(invalid="ignore"):
            ok &= np.isfinite(c).all(axis=1) & (r > 0) & np.isfinite(r) & np.isfinite(a0) & np.isfinite(a1)
        self.bad += int((~ok).sum())
        sel, c, r = sel[ok], c[ok], r[ok]
        if circle:
            xy, counts = curves.circles(c[:, 0], c[:, 1], r, self.tol, self.room())
        else:
            xy, counts = curves.arcs(c[:, 0], c[:, 1], r, a0[ok], a1[ok], self.tol, self.room())
        xyz = _ocs(np.column_stack([xy, np.repeat(c[:, 2], counts)]), self._normals(b, sel), counts)
        self._emit(b, sel, counts, np.full(len(sel), circle), xyz)

    def _ellipses(self, b: Block, sel: np.ndarray) -> None:
        c, ok = self._point(b, sel)
        major, ok2 = self._point(b, sel, (11, 21, 31))
        ratio = b.values(b.col(40)[sel])
        t0, t1 = b.values(b.col(41)[sel], 0.0), b.values(b.col(42)[sel], curves.TWO_PI)
        normal = self._normals(b, sel)
        every = np.column_stack([c, major, ratio, t0, t1, normal])
        with np.errstate(invalid="ignore"):
            ok &= (
                ok2
                & np.isfinite(every).all(axis=1)
                & (np.abs(major).sum(axis=1) > 0)
                & (np.abs(normal).sum(axis=1) > 0)
            )
        self.bad += int((~ok).sum())
        xyz, counts, closed = curves.ellipses(
            c[ok], major[ok], ratio[ok], t0[ok], t1[ok], normal[ok], self.tol, self.room()
        )
        self._emit(b, sel[ok], counts, closed, xyz)

    def _splines(self, b: Block, sel: np.ndarray) -> None:
        for r in sel.tolist():
            lo = int(b.z[r])
            hi = int(b.z[r + 1]) if r + 1 < len(b) else len(b.code)
            code, num = b.code[lo:hi], b.num[lo:hi]

            def triples(first: int, code: np.ndarray = code, num: np.ndarray = num) -> np.ndarray:
                x, y, z = (num[code == first + 10 * k] for k in range(3))
                return (
                    np.column_stack([x, y, z if len(z) == len(x) else np.zeros(len(x))])
                    if len(x) == len(y)
                    else np.empty((0, 3))
                )

            control, fit, start, stop = triples(10), triples(11), triples(12), triples(13)
            weights = num[code == 41]
            degree, flags = num[code == 71], num[code == 70]
            try:
                xyz = curves.spline(
                    control,
                    int(_whole(degree[:1], 3.0)[0]) if len(degree) else 3,
                    num[code == 40].tolist(),
                    weights.tolist() if len(weights) == len(control) else (),
                    fit,
                    [start[0].tolist(), stop[0].tolist()] if len(start) and len(stop) else None,
                    self.tol,
                    self.room(),
                    self.s.alive,
                )
            except ValueError:
                self.bad += 1
                continue
            closed = bool(len(flags) and int(_whole(flags[:1], 0.0)[0]) & 1)
            self._emit(b, np.array([r]), [len(xyz)], [closed], xyz)

    def _lwpolylines(self, b: Block, sel: np.ndarray) -> None:
        mask = np.zeros(len(b), dtype=bool)
        mask[sel] = True
        p10 = b.pairs(10, mask)
        after = np.minimum(p10 + 1, len(b.code) - 1)
        xy = np.column_stack([b.num[p10], np.where(b.code[after] == 20, b.num[after], np.nan)])
        owner = b.rec[p10]
        counts = np.searchsorted(owner, sel, "right") - np.searchsorted(owner, sel, "left")
        bulge = np.zeros(len(p10))
        p42 = b.pairs(42, mask)
        vertex = np.searchsorted(p10, p42) - 1  # the vertex a bulge follows
        mine = (vertex >= 0) & (owner[np.maximum(vertex, 0)] == b.rec[p42])
        bulge[vertex[mine]] = b.num[p42[mine]]
        ords, layer, aci, true = self._common(b, sel)
        flags = _whole(b.values(b.col(70)[sel], 0.0), 0.0).astype(np.int64)
        z = np.repeat(b.values(b.col(38)[sel], 0.0), counts)
        self._flat(ords, layer, aci, true, counts, (flags & 1) != 0, xy, z, bulge, self._normals(b, sel))

    def _flat(
        self,
        ords: np.ndarray,
        layer: np.ndarray,
        aci: np.ndarray,
        true: np.ndarray,
        counts: np.ndarray,
        closed: np.ndarray,
        xy: np.ndarray,
        z: np.ndarray,
        bulge: np.ndarray,
        normals: np.ndarray,
    ) -> None:
        """Polylines in their planes (a normal of NaN — already in the world system): arcs, then OCS → WCS."""
        if bulge.any():
            xy, counts, origin = curves.bulges(xy, bulge, counts, closed, self.tol, self.room())
            z = z[origin]
        xyz = _ocs(np.column_stack([xy, z]), normals, counts)
        self._add(ords, layer, aci, true, counts, closed, xyz)

    def _polylines(self, b: Block, ent: np.ndarray, model: np.ndarray) -> None:
        """POLYLINE records with the VERTEX records that follow each; the run may go on in the next block."""
        kind = b.kind
        poly = np.flatnonzero((kind == ds.K_POLYLINE) & ent)
        if self.open is None and len(poly) == 0:
            return
        is_vertex = kind == ds.K_VERTEX
        before = np.maximum.accumulate(np.where(is_vertex, -1, np.arange(len(kind))))  # the last record that is not one
        vrec = np.flatnonzero(is_vertex)
        owner = before[vrec]  # −1: the vertices that continue the polyline of the previous block
        cols = [b.col(c)[vrec] for c in (10, 20, 30, 42, 70)]
        v = np.column_stack(
            [
                b.values(cols[0]),
                b.values(cols[1]),
                b.values(cols[2], 0.0),
                b.values(cols[3], 0.0),
                _whole(b.values(cols[4], 0.0), 0.0),
                (cols[0] >= 0) & (cols[1] >= 0),
            ]
        )
        if self.open is not None:
            self.open[1].append(v[: int(np.searchsorted(owner, 0))])
            if before[-1] >= 0:  # another record came: the polyline is complete
                head, parts = self.open
                self.open = None
                self._runs(head[None, :], np.array([sum(len(p) for p in parts)]), np.vstack(parts))
        if len(poly) == 0:
            return
        ords, layer, aci, true = self._common(b, poly)
        head = np.column_stack(
            [
                ords,
                layer,
                aci,
                true,
                _whole(b.values(b.col(70)[poly], 0.0), 0.0),
                b.values(b.col(30)[poly], 0.0),
                self._normals(b, poly),
                model[poly],
            ]
        )
        lo, hi = np.searchsorted(owner, poly, "left"), np.searchsorted(owner, poly, "right")
        if before[-1] == poly[-1]:  # the block ends inside the last polyline
            self.open = (head[-1], [v[lo[-1] : hi[-1]]])
            head, lo, hi = head[:-1], lo[:-1], hi[:-1]
        if len(head):
            self._runs(head, hi - lo, v[ranges(lo, hi - lo)])

    def _runs(self, head: np.ndarray, counts: np.ndarray, v: np.ndarray) -> None:
        """Polylines (`head`: record, layer, ACI, true colour, flags, elevation, normal, in model space) with
        their vertices (`v`: x, y, z, bulge, flags, has X and Y)."""
        flags = head[:, 4].astype(np.int64)
        mesh = (flags & (16 | 64)) != 0
        model = head[:, 9] != 0
        self.meshes += int((mesh & model).sum())
        owner = np.repeat(np.arange(len(head)), counts)
        keep = (model & ~mesh)[owner] & ((v[:, 4].astype(np.int64) & 16) == 0)  # 16: a control point of a spline fit
        self.dropped += int((keep & (v[:, 5] == 0)).sum())
        keep &= v[:, 5] != 0
        counts = np.bincount(owner[keep], minlength=len(head))
        v, owner = v[keep], owner[keep]
        solid = ((flags & 8) != 0)[owner]  # a 3D polyline: vertices in the world system, no arcs
        normals = np.where(((flags & 8) != 0)[:, None], np.nan, head[:, 6:9])
        self._flat(
            head[:, 0].astype(np.int64),
            head[:, 1].astype(np.int32),
            head[:, 2],
            head[:, 3],
            counts,
            (flags & 1) != 0,
            v[:, :2],
            np.where(solid, v[:, 2], head[owner, 5]),
            np.where(solid, 0.0, v[:, 3]),
            normals,
        )

    # --- the result ----------------------------------------------------------------------------------

    def result(self) -> DxfRead:
        s = self.s
        warnings: list[str] = []
        records = sum(len(x.counts) for x in self.batches)
        names = {i: s.text(raw) or "0" for raw, i in self.names.items()}
        tables = {i: self.table.get(raw) for raw, i in self.names.items()}
        layers = build_layers(self.batches, names, tables, self.color)
        self.batches = []
        skipped = {k.decode("latin-1"): v for k, v in sorted(self.skipped.items()) if v}
        if not s.encoding_known and (s.replaced or any(not k.isascii() for k in self.names)):
            warnings.append(f"Кодовая страница в файле не указана: тексты прочитаны как {s.encoding}")
        if s.replaced:
            warnings.append(f"{n_text(s.replaced)} символов не читаются в кодировке {s.encoding}")
        if self.bad:
            warnings.append(f"{n_text(self.bad)} объектов без координат или с неверными размерами пропущены")
        if self.dropped:
            warnings.append(f"{n_text(self.dropped)} вершин без координаты X или Y пропущены")
        if self.no_z:
            warnings.append(f"{n_text(self.no_z)} вершин с высотой Z, которая не является числом: высота пуста")
        if skipped.get("INSERT"):
            warnings.append(
                f"Вставки блоков (INSERT) не загружаются: {n_text(skipped['INSERT'])}. "
                "Расчлените блоки в исходной программе (в AutoCAD — команда EXPLODE)"
            )
        rest = {k: v for k, v in skipped.items() if k not in ("INSERT", "PAPERSPACE")}
        if rest:
            shown = ", ".join(f"{k} — {n_text(v)}" for k, v in list(rest.items())[:8])
            warnings.append(f"Не загружены объекты других типов: {shown}{' и другие' if len(rest) > 8 else ''}")
        if skipped.get("PAPERSPACE"):
            warnings.append(f"Объекты листов (пространство листа) не загружаются: {n_text(skipped['PAPERSPACE'])}")
        table = {s.text(k): color_of(-1, -1, v) for k, v in self.table.items()}
        header = DxfHeader(s.version, s.codepage, table)
        return DxfRead("dxf", layers, header, s.encoding, records, warnings, skipped, self.meshes)


def _read(path: Path, tick: Progress, tol: float, limits: Limits, salvage: bool = False) -> DxfRead:
    def reading(fraction: float, message: str | None = None) -> None:
        tick(fraction * 0.9, message)

    stream = DxfStream(path, reading, max_record_bytes=limits.record_bytes, salvage=salvage)
    reader = _Reader(stream, tol, limits)
    with np.errstate(all="ignore"):  # a hostile file holds any numbers: what overflows is dropped as not finite
        for block in stream:
            reader.feed(block)
        if reader.open is not None:  # a file cut inside a polyline: the vertices that are there
            head, parts = reader.open
            reader._runs(head[None, :], np.array([sum(len(p) for p in parts)]), np.vstack(parts))
        tick(0.9, "Сборка слоёв")
        out = reader.result()
    tick(1.0, f"Прочитано объектов: {n_text(out.records)}")
    return out


def read_dxf(
    path: str | Path,
    progress: Progress | None = None,
    *,
    recover: bool | None = None,
    tolerance: float = curves.TOL,
    limits: Limits | None = None,
) -> DxfRead:
    """Read the lines, points and texts of a DXF file into layers (see the module docstring)."""
    path = Path(path)
    tick = progress or no_progress
    limits = limits or Limits()
    problem: FormatError | None = None
    if recover is not True:
        try:
            return _read(path, tick, tolerance, limits)
        except FormatError as exc:
            if recover is False or exc.code not in ("binary", "broken", "truncated"):
                raise
            problem = exc
    if problem is not None and problem.code == "truncated":
        out = _read(path, tick, tolerance, limits, salvage=True)
        if out.records == 0 and out.meshes == 0:  # nothing was saved: the cut file is an error
            raise problem
        out.warnings.insert(0, "Файл обрезан: прочитаны объекты до места обрыва, последний объект пропущен")
    else:
        # (a cancelled reading leaves ezdxf at work in its thread: the folder is removed when it can be)
        with tempfile.TemporaryDirectory(prefix="bf-dxf-", ignore_cleanup_errors=True) as folder:
            clean = Path(folder) / "recovered.dxf"
            try:
                ds.repair(path, clean, tick, limits.recover_bytes)
                out = _read(clean, tick, tolerance, limits)
            except FormatError as exc:
                if problem is None or problem.code == "binary" or exc.code == "too_large":
                    raise
                raise problem from exc
        if problem is not None and problem.code == "broken":
            text = f"Файл повреждён ({problem.message.rstrip('.')}): он прочитан в режиме восстановления"
            out.warnings.insert(0, f"{text} — проверьте, что все объекты на месте")
    out.recovered = True
    return out
