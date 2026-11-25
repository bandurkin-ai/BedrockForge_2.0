"""The random path of a realization (ТЗ 8.8.1: «случайный путь по родителям (зерно)»; ADR 0024 §9; Э18б, 18.30).

    rng = generator(seed, n_real, r)             the stream of realization r: PCG64(SeedSequence(seed).spawn(n_real)[r])
    path = random_path(n, rng)                   a permutation of 0…n−1: every node once, in the visiting order
    path, rng = realization_path(seed, n_real, r, n)     the path, then the same stream goes on (the draws of the nodes)

The stream of a realization depends on (seed, r) only — not on the number of threads, on the order in which the
realizations are run, nor on `n_real` (child r of a `SeedSequence` has the spawn key (r,) whoever asks for it). So the
same seed gives the same bits with 1, 2, 4 or 8 threads (Т8.8-13). The permutation is `Generator.permutation`; the
numpy version is pinned (ADR 0001): the stream is part of the contract of a seed.

One level of the grid, as in the contract of the stage (a random path over every node of the targets): GSLIB `sgsim`
visits a coarse grid first (multiple grids) to carry the long range into the first nodes. Here the template of the
nodes is the whole search ellipsoid (`nodes.spiral_template`), so a node of the sparse beginning of the path finds its
simulated neighbours as far as the range, and the cost of the sparse beginning is a scan of the template, not a
search of the grid (≈ 12·(1 + ln(k/12)) offsets a node on average).
"""

from __future__ import annotations

import numpy as np

from bf_core.geostat.estimate.store_base import bad

SEED_MAX = 2**63 - 1
N_REAL_MAX = 100  # Д-23: 1–100 realizations (20 by default)


def _is_int(v: object) -> bool:
    return isinstance(v, int | np.integer) and not isinstance(v, bool)


def check_seed(seed: object) -> int:
    """The seed of a request: an integer 0 … 2⁶³−1 (a bool or a float is not one); 422 `bad_param` {seed}."""
    if not _is_int(seed) or not 0 <= int(seed) <= SEED_MAX:  # type: ignore[call-overload]
        raise bad("seed", "Зерно: нужно целое число от 0 до 2^63 − 1.", "Задайте целое число, например 12345.")
    return int(seed)  # type: ignore[call-overload,no-any-return]


def check_n_real(n_real: object) -> int:
    """The number of realizations: an integer 1–100; 422 `bad_param` {n_real}."""
    if not _is_int(n_real) or not 1 <= int(n_real) <= N_REAL_MAX:  # type: ignore[call-overload]
        raise bad("n_real", f"Число реализаций: нужно целое число от 1 до {N_REAL_MAX}.", "Задайте, например, 20.")
    return int(n_real)  # type: ignore[call-overload,no-any-return]


def generator(seed: int, n_real: int, r: int) -> np.random.Generator:
    """The stream of the realization `r` (from 0): `PCG64(SeedSequence(seed).spawn(n_real)[r])`, without spawning
    the others (the same state)."""
    seed, n_real = check_seed(seed), check_n_real(n_real)
    if not _is_int(r) or not 0 <= int(r) < n_real:
        raise bad("r", f"Номер реализации {r}: допустимо 0–{n_real - 1}.", "Проверьте число реализаций.")
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence(seed, spawn_key=(int(r),))))


def random_path(n: int, rng: np.random.Generator) -> np.ndarray:
    """A random permutation of 0…n−1 (intp): the order in which the nodes are simulated."""
    if not _is_int(n) or n < 0:
        raise bad("n", "Число узлов пути: нужно целое число, не меньше нуля.", "Проверьте число целей модели.")
    return rng.permutation(int(n)).astype(np.intp, copy=False)


def realization_path(seed: int, n_real: int, r: int, n: int) -> tuple[np.ndarray, np.random.Generator]:
    """The path of the realization `r` and its stream, positioned after the path: the draws of the nodes follow."""
    rng = generator(seed, n_real, r)
    return random_path(n, rng), rng
