"""`solids.canonical` (подшаг 19.6; ADR 0026 решение 14): the canonical form of a mesh — vertices by (x, y, z), a
triangle turned to its smallest number with the orientation kept, the triangles sorted — and the boolean results of
manifold3d that are the same bytes under 1, 2 and 4 threads (the CPUs the process may run on: the width of oneTBB)."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from bf_core.geo import solids
from bf_core.geo.solids import boolean, canonical
from tests.solidutil import OFFSET, assert_good_solid, box, sphere, volume

CORE = Path(__file__).resolve().parents[1]


def shuffled(v: np.ndarray, t: np.ndarray, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """The same surface with the vertices permuted, the triangles permuted and every one turned cyclically."""
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(v))
    rank = np.empty(len(v), dtype=np.int64)
    rank[perm] = np.arange(len(v))
    nt = rank[t][rng.permutation(len(t))]
    shift = rng.integers(0, 3, size=len(nt))
    nt = np.take_along_axis(nt, (shift[:, None] + np.arange(3)) % 3, axis=1)
    return v[perm], nt.astype(np.int32)


def test_canonical_is_order_independent_and_keeps_the_surface() -> None:
    v, t = sphere(20.0, 12, 24)
    a = canonical(v, t)
    for seed in (1, 2, 3):
        b = canonical(*shuffled(v, t, seed))
        assert a[0].tobytes() == b[0].tobytes() and a[1].tobytes() == b[1].tobytes()
    cv, ct = a
    assert cv.dtype == np.float64 and ct.dtype == np.int32
    assert len(cv) == len(v) and len(ct) == len(t)
    assert volume((cv, ct)) == pytest.approx(volume((v, t)), rel=1e-12)  # the orientation kept
    assert_good_solid((cv, ct))


def test_canonical_form_is_sorted_and_rotated() -> None:
    v, t = box((0, 0, 0), (3, 2, 1))
    cv, ct = canonical(*shuffled(v, t, 7))
    keys = list(map(tuple, cv))
    assert keys == sorted(keys)  # vertices by (x, y, z)
    assert (ct[:, 0] == ct.min(axis=1)).all()  # the smallest number first
    rows = list(map(tuple, ct))
    assert rows == sorted(rows)
    assert canonical(cv, ct)[1].tobytes() == ct.tobytes()  # idempotent


def test_canonical_drops_unused_vertices_and_the_sign_of_zero() -> None:
    v = np.array([[0.0, 0.0, 0.0], [-0.0, 1.0, 0.0], [1.0, 0.0, -0.0], [9.0, 9.0, 9.0]])
    t = np.array([[2, 1, 0]])
    cv, ct = canonical(v, t)
    assert len(cv) == 3 and ct.tolist() == [[0, 2, 1]]  # (0,0,0) (0,1,0) (1,0,0): 2,1,0 → 0,2,1 in the same sense
    assert not np.signbit(cv).any()
    ev, et = canonical(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32))
    assert ev.shape == (0, 3) and et.shape == (0, 3) and et.dtype == np.int32


def test_canonical_refuses_a_broken_mesh_in_russian() -> None:
    with pytest.raises(solids.SolidError) as e:
        canonical(np.zeros((3, 3)), np.array([[0, 1, 5]]))
    assert e.value.code == "bad_mesh" and "Результат" in e.value.message


CHILD = r"""
import hashlib, os, sys
os.sched_setaffinity(0, set(sorted(os.sched_getaffinity(0))[:int(sys.argv[1])]))
import numpy as np
from bf_core.geo import solids
from tests.solidutil import box, sphere
a = sphere(30.0, 60, 120, centre=(2.0, 1.0, 0.5))
b = sphere(24.0, 48, 96, centre=(14.0, 3.0, -2.0))
c = box((-8, -40, -40), (40, 40, 12))
h = hashlib.sha256()
for ops in (("union", a, b), ("intersection", a, b), ("difference", a, b), ("difference", a, c), ("union", b, c)):
    v, t, _ = solids.boolean(ops[1], ops[2], ops[0])
    v, t = solids.canonical(v, t)
    h.update(v.tobytes()); h.update(t.tobytes())
print(h.hexdigest(), len(os.sched_getaffinity(0)))
"""


@pytest.mark.skipif(not hasattr(os, "sched_setaffinity"), reason="the width of oneTBB is the CPU mask: Linux")
def test_boolean_results_are_bitwise_under_1_2_4_threads() -> None:
    cpus = len(os.sched_getaffinity(0))
    env = {**os.environ, "PYTHONPATH": str(CORE), "BF_NO_WARMUP": "1"}
    digests = {}
    for n in (1, 2, 4):
        run = subprocess.run([sys.executable, "-c", CHILD, str(n)], cwd=CORE, env=env, capture_output=True, text=True,
                             timeout=300)  # fmt: skip
        assert run.returncode == 0, run.stderr[-600:]
        digest, width = run.stdout.split()
        assert int(width) == min(n, cpus)
        digests[n] = digest
    assert len(set(digests.values())) == 1, digests


def test_canonical_of_a_boolean_matches_shuffled_input() -> None:
    """Two inputs of the same bodies in another order: the booleans may differ in order, canonical makes them one."""
    a, b = sphere(10.0, 16, 32), sphere(8.0, 16, 32, centre=(6.0, 0.0, 0.0))
    first = canonical(*boolean(a, b, "union")[:2])
    second = canonical(*boolean(shuffled(*a, 4), shuffled(*b, 5), "union")[:2])
    assert first[0].tobytes() == second[0].tobytes() and first[1].tobytes() == second[1].tobytes()
    assert hashlib.sha256(first[0].tobytes()).hexdigest() == hashlib.sha256(second[0].tobytes()).hexdigest()
    assert abs(volume(first)) > 0 and OFFSET.shape == (3,)
