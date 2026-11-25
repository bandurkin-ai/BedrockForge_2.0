"""The random path of a simulation (ТЗ 8.8.1, Т8.8-13; ADR 0024 §9; Э18б, 18.30): the seed gives the same path bit for
bit between runs (a fresh process too) and with any number of threads; every node is visited exactly once; the stream
of a realization is `PCG64(SeedSequence(seed).spawn(n_real)[r])`; the checks of the seed, the number of realizations
and the index."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.geostat.simulation import path as pa

SCRIPT = """
import hashlib, sys
from bf_core.geostat.simulation import path as pa
p, rng = pa.realization_path(12345, 20, 3, 5000)
sys.stdout.write(hashlib.sha256(p.tobytes() + rng.random(4).tobytes()).hexdigest())
"""


def digest(seed: int, n_real: int, r: int, n: int) -> str:
    p, rng = pa.realization_path(seed, n_real, r, n)
    return hashlib.sha256(p.tobytes() + rng.random(4).tobytes()).hexdigest()


def test_same_seed_same_bits() -> None:
    a = pa.realization_path(12345, 20, 3, 5000)
    b = pa.realization_path(12345, 20, 3, 5000)
    assert a[0].tobytes() == b[0].tobytes()
    assert a[1].random(8).tobytes() == b[1].random(8).tobytes()  # the stream goes on identically after the path


def test_same_bits_in_a_fresh_process() -> None:
    out = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, check=True).stdout
    assert out == digest(12345, 20, 3, 5000)


def test_pinned_start() -> None:
    """The numpy stream is part of the contract of a seed (ADR 0001 pins the version): a change shows here."""
    p = pa.random_path(1000, pa.generator(12345, 20, 0))
    assert p[:8].tolist() == PINNED


def test_other_seed_or_realization_differs() -> None:
    base = pa.realization_path(12345, 20, 0, 1000)[0]
    assert not np.array_equal(base, pa.realization_path(12346, 20, 0, 1000)[0])
    assert not np.array_equal(base, pa.realization_path(12345, 20, 1, 1000)[0])


@pytest.mark.parametrize("threads", [1, 2, 4, 8])
def test_independent_of_threads(threads: int) -> None:
    """Т8.8-13: the realizations run on `threads` threads in any order give the paths of the serial run."""
    n_real = 24
    serial = [digest(777, n_real, r, 3000) for r in range(n_real)]
    order = list(range(n_real))[::-1]
    with ThreadPoolExecutor(threads) as ex:
        got = dict(zip(order, ex.map(lambda r: digest(777, n_real, r, 3000), order), strict=True))
    assert [got[r] for r in range(n_real)] == serial


def test_stream_is_the_spawned_child() -> None:
    """generator(seed, n, r) = PCG64(SeedSequence(seed).spawn(n)[r]); the child does not depend on n."""
    for n_real, r in ((20, 0), (20, 7), (20, 19), (5, 4), (100, 63)):
        ref = np.random.Generator(np.random.PCG64(np.random.SeedSequence(2**62 + 5).spawn(n_real)[r]))
        got = pa.generator(2**62 + 5, n_real, r)
        assert got.random(16).tobytes() == ref.random(16).tobytes()
    a = pa.generator(9, 20, 2).random(4)
    b = pa.generator(9, 100, 2).random(4)
    assert a.tobytes() == b.tobytes()


@pytest.mark.parametrize("n", [0, 1, 2, 7, 1000, 100_001])
def test_every_node_exactly_once(n: int) -> None:
    p = pa.random_path(n, pa.generator(1, 3, 1))
    assert p.dtype == np.intp
    assert p.shape == (n,)
    assert np.array_equal(np.sort(p), np.arange(n))


def test_path_is_shuffled() -> None:
    p = pa.random_path(10_000, pa.generator(1, 3, 1))
    assert not np.array_equal(p, np.arange(10_000))
    assert abs(np.corrcoef(p, np.arange(10_000))[0, 1]) < 0.05  # no order left


@pytest.mark.parametrize("seed", [-1, 2**63, 1.5, True, "12345", None])
def test_bad_seed(seed: object) -> None:
    with pytest.raises(ApiError) as e:
        pa.check_seed(seed)
    assert e.value.status == 422 and e.value.code == "bad_param" and e.value.extra["param"] == "seed"
    assert e.value.hint


def test_seed_limits() -> None:
    assert pa.check_seed(0) == 0
    assert pa.check_seed(2**63 - 1) == 2**63 - 1
    assert pa.check_seed(np.int64(5)) == 5
    pa.generator(2**63 - 1, 1, 0)


@pytest.mark.parametrize("n_real", [0, 101, 2.0, False, None])
def test_bad_n_real(n_real: object) -> None:
    with pytest.raises(ApiError) as e:
        pa.check_n_real(n_real)
    assert e.value.code == "bad_param" and e.value.extra["param"] == "n_real"


@pytest.mark.parametrize("r", [-1, 20, 1.0, True])
def test_bad_realization_index(r: object) -> None:
    with pytest.raises(ApiError) as e:
        pa.generator(1, 20, r)  # type: ignore[arg-type]
    assert e.value.code == "bad_param" and e.value.extra["param"] == "r"


def test_bad_length() -> None:
    with pytest.raises(ApiError):
        pa.random_path(-1, pa.generator(1, 1, 0))


PINNED = [957, 913, 686, 816, 543, 402, 859, 198]
