"""Simulation of grades: SGS, SIS, co-SGS (ТЗ 8.8, Д-23; ADR 0024 §9; Э18б).

`path`  — the random path over the nodes: a stream per realization from the seed, the permutation;
`nodes` — the grid of the nodes (the parents of a regular block model), the spiral template of the offsets and the
          search of the nearest simulated nodes; the conditioning samples are found by `estimate.search.Searcher`.
"""
