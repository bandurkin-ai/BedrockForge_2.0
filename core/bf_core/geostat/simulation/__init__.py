"""Simulation of grades: SGS, SIS, co-SGS (ТЗ 8.8, Д-23; ADR 0024 §9; Э18б).

`path`  — the random path over the nodes: a stream per realization from the seed, the permutation;
`nodes` — the grid of the nodes (the parents of a regular block model), the spiral template of the offsets and the
          search of the nearest simulated nodes; the conditioning samples are found by `estimate.search.Searcher`.
`sgs`   — the sequential Gaussian simulation: `simulate(SgsParams, samples, grid, centres)` → `SgsResult`.
`spec`, `store`, `store_check` — the parameters and the stored object «Моделирование» (`objects(kind="simulation")`);
`real` — the blob `real` (Parquet of the realizations); `targets` — the parents of the blocks of the domain;
`job` — the job `simulate.run`; `post` — the job `simulate.post` (E-type, σ, quantiles, P(Z ≥ t) into the fields of
the block model); `routes` — `/simulations`, `/simulate/run`, `/simulate/post` (included by `geostat/router.py`).
"""
