"""Synthetic closed meshes in UTM coordinates (float64 vertices, uint32 triangles)."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def uv_sphere(n_triangles: int, radius: float = 100.0, centre=(500_000.0, 7_000_000.0, 300.0)):  # type: ignore[no-untyped-def]
    """Closed UV sphere with about n_triangles triangles. Returns (vertices, triangles)."""
    # 2*nu*(nv-1) triangles for nu segments and nv rings
    nv = max(3, int(np.sqrt(n_triangles / 4)))
    nu = max(3, n_triangles // (2 * (nv - 1)))
    theta = np.linspace(0.0, np.pi, nv + 1)[1:-1]
    phi = np.linspace(0.0, 2 * np.pi, nu, endpoint=False)
    t, p = np.meshgrid(theta, phi, indexing="ij")
    ring = np.stack([np.sin(t) * np.cos(p), np.sin(t) * np.sin(p), np.cos(t)], axis=-1).reshape(-1, 3)
    verts = np.vstack([[0, 0, 1], ring, [0, 0, -1]]) * radius + np.asarray(centre)
    top, bottom = 0, len(verts) - 1
    tris = []
    j = np.arange(nu)
    jn = (j + 1) % nu
    tris.append(np.stack([np.full(nu, top), 1 + j, 1 + jn], axis=1))
    for r in range(nv - 2):
        a = 1 + r * nu + j
        b = 1 + r * nu + jn
        c = 1 + (r + 1) * nu + j
        d = 1 + (r + 1) * nu + jn
        tris.append(np.stack([a, c, b], axis=1))
        tris.append(np.stack([b, c, d], axis=1))
    last = 1 + (nv - 2) * nu
    tris.append(np.stack([last + j, np.full(nu, bottom), last + jn], axis=1))
    return verts.astype(np.float64), np.vstack(tris).astype(np.uint32)


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a closed sphere mesh (.npz)")
    ap.add_argument("--triangles", type=int, default=1_000_000)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    v, t = uv_sphere(a.triangles)
    np.savez(a.out, vertices=v, triangles=t)
    print(f"{a.out}: {len(v)} vertices, {len(t)} triangles")


if __name__ == "__main__":
    main()
