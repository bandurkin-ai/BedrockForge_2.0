"""Series of sections (ТЗ 8.1; ADR 0016).

Geometry (no numba at import: the API imports these modules before BF-READY):

    geometry.py   axes ŝ, v̂, ŵ, the number of sections K, s_k, O_k, (u, w, t), the windows, the section of a point
                  and of a contour — `series_geometry(meta)`, `count(s0, s1, step)`, `SeriesError`
    polyline.py   a polyline section: the segment of a point, the development u, the four half-spaces of a segment
    names.py      the template of the names (a safe subset of the Python format), the defaults, the table
    kernels.py    the numba kernels of the two above (imported lazily, by the bulk functions only)

The storage of a series, its API and the rest are in the other modules of the package (ADR 0016 §3).
The answers shared with the 3D window: `core/tests/data/sections/series_cases.json`.
"""
