"""The ground filter, stage 8 (`geo/ground.py`, ADR 0014 §4, ТЗ 7.35 Д-22, idea 4): returns on a face steeper
than any height field — a vertical or overhanging wall of a bench.

The grid of lowest returns holds one height a cell: of a vertical wall it keeps the foot, and the points of the
wall stand metres above the ground surface. They are returned here.

Candidates are the points that are neither ground nor noise and have ground both higher and lower than
themselves within R_б (a coarse grid of the highest and the lowest ground height sorts out the vegetation of
flat ground at once). The candidates are gathered into voxels of c × c × c (c = CR/2); the plane of a voxel
is the principal plane of the voxel centres within 1.5·CR (2.5·CR where there are fewer than 12 of them: a
sparse wall, its upper and lower edge) — so the cost does not grow with the density of the cloud or the height
of the wall. A voxel is a face when

  * the patch is flat: at least 6 voxels, their spread across the plane ≤ CT/2 and much less than along it;
  * the plane is steeper than `face` (60°);
  * the plane meets ground above and below the voxel: a ground cell within R_б in plan (and within R_б/2 along
    the strike of the plane — straight up and down the face) whose plane, continued half a cell beyond the
    cell, passes within CT of the plane of the face; one such cell higher than the voxel and one lower.

A wall of a bench has a crest and a foot. A facade has a foot but a roof instead of a crest, a crown has
neither, vegetation on a slope is not flat. The points of a face voxel within CT/2 of its plane become ground
(class 2) with h = their distance to the plane.

Ground is taken from the cells, not searched among the points, and the voxels hold only candidates: next to
the foot the patch is half a disc of wall, not an angle of wall and berm.
"""

from __future__ import annotations

import math

import numpy as np

from bf_core.jit import njit

MIN_VOXELS = 6  # voxels of a flat patch


@njit(cache=True, nogil=True)
def _plane_of(a00, a01, a02, a11, a12, a22):  # type: ignore[no-untyped-def]
    """Symmetric 3 × 3 covariance → (l0, l1, nx, ny, nz): the two smallest eigenvalues and the unit eigenvector
    of the smallest (the normal of the principal plane). Closed form; l0 is then taken as nᵀ·A·n."""
    p1 = a01 * a01 + a02 * a02 + a12 * a12
    q = (a00 + a11 + a22) / 3.0
    p2 = (a00 - q) ** 2 + (a11 - q) ** 2 + (a22 - q) ** 2 + 2.0 * p1
    if p2 <= 0.0:
        return q, q, 0.0, 0.0, 1.0
    p = math.sqrt(p2 / 6.0)
    b00 = (a00 - q) / p
    b11 = (a11 - q) / p
    b22 = (a22 - q) / p
    b01 = a01 / p
    b02 = a02 / p
    b12 = a12 / p
    r = 0.5 * (b00 * (b11 * b22 - b12 * b12) - b01 * (b01 * b22 - b12 * b02) + b02 * (b01 * b12 - b11 * b02))
    r = min(max(r, -1.0), 1.0)
    phi = math.acos(r) / 3.0
    l2 = q + 2.0 * p * math.cos(phi)
    l0 = q + 2.0 * p * math.cos(phi + 2.0 * math.pi / 3.0)
    l1 = 3.0 * q - l0 - l2
    # the eigenvector: the cross product of two rows of A − l0·I, the longest of the three
    r0x, r0y, r0z = a00 - l0, a01, a02
    r1x, r1y, r1z = a01, a11 - l0, a12
    r2x, r2y, r2z = a02, a12, a22 - l0
    ax, ay, az = r0y * r1z - r0z * r1y, r0z * r1x - r0x * r1z, r0x * r1y - r0y * r1x
    bx, by, bz = r0y * r2z - r0z * r2y, r0z * r2x - r0x * r2z, r0x * r2y - r0y * r2x
    cx, cy, cz = r1y * r2z - r1z * r2y, r1z * r2x - r1x * r2z, r1x * r2y - r1y * r2x
    na = ax * ax + ay * ay + az * az
    nb = bx * bx + by * by + bz * bz
    nc = cx * cx + cy * cy + cz * cz
    if nb > na:
        ax, ay, az, na = bx, by, bz, nb
    if nc > na:
        ax, ay, az, na = cx, cy, cz, nc
    if na <= 0.0:
        return l0, l1, 0.0, 0.0, 1.0
    s = 1.0 / math.sqrt(na)
    ax *= s
    ay *= s
    az *= s
    l0 = (
        ax * (a00 * ax + a01 * ay + a02 * az)
        + ay * (a01 * ax + a11 * ay + a12 * az)
        + az * (a02 * ax + a12 * ay + a22 * az)
    )
    return max(l0, 0.0), l1, ax, ay, az


@njit(cache=True, nogil=True)
def _first_bin(vbin, lo, hi, value):  # type: ignore[no-untyped-def]
    """The first voxel of vbin[lo:hi] (ascending) with a bin ≥ value."""
    while lo < hi:
        mid = (lo + hi) // 2
        if vbin[mid] < value:
            lo = mid + 1
        else:
            hi = mid
    return lo


@njit(cache=True, nogil=True)
def _span_of(high, low, cj, ci, kb, ct):  # type: ignore[no-untyped-def]
    """(top, bottom): a candidate of the cell is not higher than the highest ground of the coarse squares
    around it plus CT and not lower than the lowest minus CT. No ground there — (-inf, inf): no candidates."""
    top = -np.inf
    bottom = np.inf
    for bj in range(max(cj // kb - 1, 0), min(cj // kb + 2, high.shape[0])):
        for bi in range(max(ci // kb - 1, 0), min(ci // kb + 2, high.shape[1])):
            top = max(top, high[bj, bi])
            bottom = min(bottom, low[bj, bi])
    if top == -np.inf:
        return -np.inf, np.inf
    return top + ct, bottom - ct


@njit(cache=True, nogil=True)
def _bins_of(xyz, order, pcls, s, e, top, bottom, c, bins):  # type: ignore[no-untyped-def]
    """Vertical bins of the candidates of a cell, ascending, into `bins` → how many (with repeats)."""
    n = 0
    for k in range(s, e):
        cl = pcls[k]
        if cl == 2 or cl == 7:
            continue
        z = xyz[order[k], 2]
        if z > top or z < bottom:
            continue
        bins[n] = int(math.floor(z / c))
        n += 1
    if n > 48:
        bins[:n] = np.sort(bins[:n])
    else:
        for i in range(1, n):
            v = bins[i]
            j = i - 1
            while j >= 0 and bins[j] > v:
                bins[j + 1] = bins[j]
                j -= 1
            bins[j + 1] = v
    return n


@njit(cache=True, nogil=True)
def relief(state, g, start, ncx, ncy, kb):  # type: ignore[no-untyped-def]
    """The highest and the lowest ground of coarse squares of kb cells, and the longest cell (in points)."""
    mbx = (ncx + kb - 1) // kb
    mby = (ncy + kb - 1) // kb
    high = np.full((mby, mbx), -np.inf)
    low = np.full((mby, mbx), np.inf)
    longest = 0
    for cj in range(ncy):
        for ci in range(ncx):
            cell = cj * ncx + ci
            if start[cell + 1] - start[cell] > longest:
                longest = start[cell + 1] - start[cell]
            if state[cell] == 1:
                v = g[cell]
                if v > high[cj // kb, ci // kb]:
                    high[cj // kb, ci // kb] = v
                if v < low[cj // kb, ci // kb]:
                    low[cj // kb, ci // kb] = v
    return high, low, longest


@njit(cache=True, nogil=True)
def voxels(xyz, order, start, pcls, high, low, longest, grid, kb, ct, box):  # type: ignore[no-untyped-def]
    """The candidates of the cells of `box` (i0, j0, i1, j1) gathered into voxels of c × c × c.

    → (vstart, vbin, vx, vy, vz): the voxels of a cell are vstart[cell] : vstart[cell + 1], ascending in the
    vertical bin vbin; vx, vy, vz — the mean point of a voxel (X, Y from the origin of the cloud).
    """
    ncx, ncy, ox, oy, c = grid
    i0, j0, i1, j1 = box
    bins = np.empty(max(longest, 1), np.int64)
    vstart = np.zeros(ncx * ncy + 1, np.int32)
    for cj in range(j0, j1):
        for ci in range(i0, i1):
            cell = cj * ncx + ci
            if start[cell] == start[cell + 1]:
                continue
            top, bottom = _span_of(high, low, cj, ci, kb, ct)
            n = _bins_of(xyz, order, pcls, start[cell], start[cell + 1], top, bottom, c, bins)
            count = 0
            for k in range(n):
                if k == 0 or bins[k] != bins[k - 1]:
                    count += 1
            vstart[cell + 1] = count
    for cell in range(ncx * ncy):
        vstart[cell + 1] += vstart[cell]
    nv = vstart[ncx * ncy]
    vbin = np.empty(nv, np.int64)
    vx = np.zeros(nv)
    vy = np.zeros(nv)
    vz = np.zeros(nv)
    vn = np.zeros(nv, np.int32)
    for cj in range(j0, j1):
        for ci in range(i0, i1):
            cell = cj * ncx + ci
            a = vstart[cell]
            b = vstart[cell + 1]
            if a == b:
                continue
            top, bottom = _span_of(high, low, cj, ci, kb, ct)
            n = _bins_of(xyz, order, pcls, start[cell], start[cell + 1], top, bottom, c, bins)
            v = a
            for k in range(n):
                if k == 0 or bins[k] != bins[k - 1]:
                    vbin[v] = bins[k]
                    v += 1
            for k in range(start[cell], start[cell + 1]):
                cl = pcls[k]
                if cl == 2 or cl == 7:
                    continue
                i = order[k]
                z = xyz[i, 2]
                if z > top or z < bottom:
                    continue
                v = _first_bin(vbin, a, b, int(math.floor(z / c)))
                vx[v] += xyz[i, 0] - ox
                vy[v] += xyz[i, 1] - oy
                vz[v] += z
                vn[v] += 1
    for v in range(nv):
        vx[v] /= vn[v]
        vy[v] /= vn[v]
        vz[v] /= vn[v]
    return vstart, vbin, vx, vy, vz


@njit(cache=True, nogil=True)
def _patch(v, cj, ci, reach, radius2, vox, ncx, box):  # type: ignore[no-untyped-def]
    """The voxels within √radius2 of voxel v (reach cells and bins around) → (count, the mean offset from v,
    the covariance of the offsets: xx, xy, xz, yy, yz, zz)."""
    vstart, vbin, vx, vy, vz = vox
    i0, j0, i1, j1 = box
    x0 = vx[v]
    y0 = vy[v]
    z0 = vz[v]
    cnt = 0
    sx = 0.0
    sy = 0.0
    sz = 0.0
    sxx = 0.0
    sxy = 0.0
    sxz = 0.0
    syy = 0.0
    syz = 0.0
    szz = 0.0
    for jj in range(max(cj - reach, j0), min(cj + reach + 1, j1)):
        for ii in range(max(ci - reach, i0), min(ci + reach + 1, i1)):
            other = jj * ncx + ii
            a = vstart[other]
            b = vstart[other + 1]
            if a == b:
                continue
            w = _first_bin(vbin, a, b, vbin[v] - reach)
            while w < b and vbin[w] <= vbin[v] + reach:
                dx = vx[w] - x0
                dy = vy[w] - y0
                dz = vz[w] - z0
                if dx * dx + dy * dy + dz * dz <= radius2:
                    cnt += 1
                    sx += dx
                    sy += dy
                    sz += dz
                    sxx += dx * dx
                    sxy += dx * dy
                    sxz += dx * dz
                    syy += dy * dy
                    syz += dy * dz
                    szz += dz * dz
                w += 1
    if cnt == 0:
        return 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    sx /= cnt
    sy /= cnt
    sz /= cnt
    return (
        cnt,
        sx,
        sy,
        sz,
        sxx / cnt - sx * sx,
        sxy / cnt - sx * sy,
        sxz / cnt - sx * sz,
        syy / cnt - sy * sy,
        syz / cnt - sy * sz,
        szz / cnt - sz * sz,
    )


@njit(cache=True, nogil=True)
def face_rows(xyz, order, start, pcls, ph, ground, relief_, vox, grid, cell0, row0, row1, cols, box, par):  # type: ignore[no-untyped-def]
    """Points of steep faces with ground above and below, rows [row0, row1) × columns `cols` of the cells
    → pcls 2, ph — the distance to the plane of the face. Returns the number of points turned to ground.

    ground = (state, g, gx, gy) of the cells; relief_ = (high, low); vox — the voxels of `box`; grid =
    (ncx, ncy, ox, oy, c); cell0 = (cx0, cy0); par = (CR, CT, R_б, cos of the face angle). A strip reads the
    voxels and the ground and writes only the points of its own rows.
    """
    state, g, gx, gy = ground
    high, low = relief_
    vstart, vbin, vx, vy, vz = vox
    ncx, ncy, _ox, _oy, c = grid
    ox = grid[2]
    oy = grid[3]
    cx0, cy0 = cell0
    cr, ct, r_b, cos_face = par
    col0, col1 = cols
    kb = int(math.ceil(r_b / c))
    turned = 0
    lateral = 0.5 * r_b
    thick2 = (0.5 * ct) ** 2
    for cj in range(row0, row1):
        for ci in range(col0, col1):
            cell = cj * ncx + ci
            if vstart[cell] == vstart[cell + 1]:
                continue
            top, bottom = _span_of(high, low, cj, ci, kb, ct)
            for v in range(vstart[cell], vstart[cell + 1]):
                # the patch: 1.5·CR around the voxel; a sparse wall (or its upper and lower edge) — 2.5·CR
                radius = 1.5 * cr
                cnt, sx, sy, sz, cxx, cxy, cxz, cyy, cyz, czz = _patch(
                    v, cj, ci, int(math.ceil(radius / c)), radius * radius, vox, ncx, box
                )
                if cnt < 2 * MIN_VOXELS:
                    radius = 2.5 * cr
                    cnt, sx, sy, sz, cxx, cxy, cxz, cyy, cyz, czz = _patch(
                        v, cj, ci, int(math.ceil(radius / c)), radius * radius, vox, ncx, box
                    )
                if cnt < MIN_VOXELS:
                    continue
                l0, l1, nx, ny, nz = _plane_of(cxx, cxy, cxz, cyy, cyz, czz)
                if abs(nz) > cos_face or l0 > thick2 or l0 > 0.1 * l1 or l1 < 0.04 * radius * radius:
                    continue
                x0 = vx[v]
                y0 = vy[v]
                z0 = vz[v]
                # the plane passes through the centre of the patch
                qx = x0 + sx
                qy = y0 + sy
                qz = z0 + sz
                flat = math.sqrt(nx * nx + ny * ny)
                tx = -ny / flat  # the strike of the face
                ty = nx / flat
                above = False
                below = False
                for jj in range(max(cj - kb, 0), min(cj + kb + 1, ncy)):
                    for ii in range(max(ci - kb, 0), min(ci + kb + 1, ncx)):
                        other = jj * ncx + ii
                        if state[other] != 1:
                            continue
                        dx = (cx0 + ii + 0.5) * c - x0
                        dy = (cy0 + jj + 0.5) * c - y0
                        if dx * dx + dy * dy > r_b * r_b or abs(tx * dx + ty * dy) > lateral:
                            continue
                        height = g[other]
                        s0 = nx * (dx + x0 - qx) + ny * (dy + y0 - qy) + nz * (height - qz)
                        # the plane of the ground cell over the cell and half a cell around it: the cell next
                        # to a vertical wall holds the foot of the wall, its ground of the upper level is a
                        # few points at the crest that have no cell of their own
                        reach = c * (abs(nx + nz * gx[other]) + abs(ny + nz * gy[other]))
                        if abs(s0) > ct + reach:
                            continue
                        if height > z0:
                            above = True
                        elif height < z0:
                            below = True
                    if above and below:
                        break
                if not (above and below):
                    continue
                for k in range(start[cell], start[cell + 1]):
                    cl = pcls[k]
                    if cl == 2 or cl == 7:
                        continue
                    i = order[k]
                    z = xyz[i, 2]
                    if z > top or z < bottom or int(math.floor(z / c)) != vbin[v]:
                        continue
                    d = abs(nx * (xyz[i, 0] - ox - qx) + ny * (xyz[i, 1] - oy - qy) + nz * (z - qz))
                    if d <= 0.5 * ct:
                        pcls[k] = 2
                        ph[k] = d
                        turned += 1
    return turned
