"""numba kernels of the wireframe readers (DXF meshes, OBJ, STL, Surpac DTM): the loops over the bytes of a text
block and over the corners of faces. Imported lazily: numba is heavy.

A text block is a uint8 array of whole lines. `scan` cuts it into tokens (runs of bytes above the space; a comma
separates as well when asked) and tells the first token of every line; the other kernels work on tokens given by
their numbers: `words` — a keyword as a number, `pack` — the texts of numbers one after another (Arrow turns them
into float64, correctly rounded), `ints` — whole numbers. `fan` turns polygons into triangles, `face_list` reads
the face list of a DXF MESH.
"""

from __future__ import annotations

import numpy as np

from bf_core.jit import njit

MISSING = np.iinfo(np.int64).min  # `ints`: the token is not a whole number


@njit(cache=True, nogil=True)
def scan(buf, n, final, comma, tstart, tend, lfirst):  # type: ignore[no-untyped-def]
    """Tokens of the whole lines of buf[:n]: `tstart`, `tend` — the bytes of every token, `lfirst[i]` — the first
    token of line i (`lfirst[lines]` — the number of tokens). The last line counts without its line feed only
    when `final`. Returns (lines, tokens, the first byte not taken)."""
    lines = 0
    tokens = 0
    pos = 0
    while pos < n:
        e = pos
        while e < n and buf[e] != 10:
            e += 1
        if e >= n and not final:
            break
        lfirst[lines] = tokens
        p = pos
        while p < e:
            c = buf[p]
            if c <= 32 or (comma and c == 44):
                p += 1
                continue
            s = p
            while p < e:
                c = buf[p]
                if c <= 32 or (comma and c == 44):
                    break
                p += 1
            tstart[tokens] = s
            tend[tokens] = p
            tokens += 1
        lines += 1
        pos = e + 1
    lfirst[lines] = tokens
    return lines, tokens, min(pos, n)


@njit(cache=True, nogil=True)
def words(buf, tstart, tend, idx, out):  # type: ignore[no-untyped-def]
    """`out[i]` — the token idx[i] as a number: its first eight bytes (ASCII letters in lower case) from the
    lowest up."""
    for i in range(len(idx)):
        s = tstart[idx[i]]
        key = np.uint64(0)
        for j in range(min(tend[idx[i]] - s, 8)):
            c = buf[s + j]
            if 65 <= c <= 90:
                c += 32
            key |= np.uint64(c) << np.uint64(8 * j)
        out[i] = key


@njit(cache=True, nogil=True)
def pack(buf, tstart, tend, idx, pk, poff):  # type: ignore[no-untyped-def]
    """The bytes of the tokens `idx` one after another in `pk`; `poff[i + 1]` — where token i ends."""
    at = 0
    for i in range(len(idx)):
        for j in range(tstart[idx[i]], tend[idx[i]]):
            pk[at] = buf[j]
            at += 1
        poff[i + 1] = at
    return at


@njit(cache=True, nogil=True)
def ints(buf, tstart, tend, idx, slash, out):  # type: ignore[no-untyped-def]
    """`out[i]` — the whole number the token idx[i] is (with `slash` — the one it starts with, up to a «/»:
    a corner `v/vt/vn` of OBJ); MISSING when it is not one (or has more than 18 digits)."""
    for i in range(len(idx)):
        p = tstart[idx[i]]
        e = tend[idx[i]]
        negative = False
        if p < e and (buf[p] == 45 or buf[p] == 43):
            negative = buf[p] == 45
            p += 1
        value = 0
        digits = 0
        while p < e and 48 <= buf[p] <= 57 and digits < 18:
            value = value * 10 + (buf[p] - 48)
            p += 1
            digits += 1
        if digits == 0 or (p < e and not (slash and buf[p] == 47)):
            out[i] = MISSING
        else:
            out[i] = -value if negative else value


@njit(cache=True, nogil=True)
def fan(corners, counts, tri, owner):  # type: ignore[no-untyped-def]
    """Polygons → triangles. `corners` — the vertex numbers of the polygons one after another (negative — not a
    vertex), `counts` — corners of every polygon. Three corners are a triangle as they are; of more, the corners
    that repeat the one before (and the last ones that repeat the first) are left out and the rest is a fan
    from the first corner. `tri` (rows) and `owner` (the polygon of every triangle) take at most
    sum(max(counts − 2, 0)) triangles. Returns (triangles, polygons left out: fewer than three different
    corners or a corner that is not a vertex)."""
    at = 0
    t = 0
    bad = 0
    for f in range(len(counts)):
        k = counts[f]
        ok = k >= 3
        if ok:
            for j in range(at, at + k):
                if corners[j] < 0:
                    ok = False
                    break
        if not ok:
            bad += 1
        elif k == 3:
            tri[t, 0] = corners[at]
            tri[t, 1] = corners[at + 1]
            tri[t, 2] = corners[at + 2]
            owner[t] = f
            t += 1
        else:
            a = corners[at]
            last = at + k - 1
            while last > at and corners[last] == a:
                last -= 1
            prev = a
            b = -1
            made = 0
            for j in range(at + 1, last + 1):
                c = corners[j]
                if c == prev:
                    continue
                if b >= 0:
                    tri[t, 0] = a
                    tri[t, 1] = b
                    tri[t, 2] = c
                    owner[t] = f
                    t += 1
                    made += 1
                b = c
                prev = c
            if made == 0:
                bad += 1
        at += k
    return t, bad


@njit(cache=True, nogil=True)
def face_list(values, vertices, counts, corners):  # type: ignore[no-untyped-def]
    """The face list of a DXF MESH — «count, corner, …, count, corner, …» as float64 — into `counts` and
    `corners` (−1: not a vertex of `vertices`). Returns (faces, corners written, the list is whole)."""
    n = len(values)
    at = 0
    faces = 0
    done = 0
    while at < n:
        v = values[at]
        if not (v >= 1.0 and v <= n) or v != np.floor(v):
            return faces, done, False
        k = int(v)
        if at + 1 + k > n:
            return faces, done, False
        for j in range(at + 1, at + 1 + k):
            c = values[j]
            if c >= 0.0 and c < vertices and c == np.floor(c):
                corners[done] = int(c)
            else:
                corners[done] = -1
            done += 1
        counts[faces] = k
        faces += 1
        at += 1 + k
    return faces, done, True
