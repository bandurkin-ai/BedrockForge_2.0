"""Third-party reference data under tests/data/{ref,ext} (ADR 0011): the files are intact, internally
consistent and still say what their manifests say. BF kernels are compared to them in the stage
that builds the kernel (Э11 OMF, Э16 variograms, Э18 kriging, Э21 pit); this file only guards the data."""

from __future__ import annotations

import gzip
import json
import struct
import zipfile
from pathlib import Path

import numpy as np
import pytest

DATA = Path(__file__).resolve().parents[2] / "tests" / "data"
REF = DATA / "ref" / "geostatspy"
EXT = DATA / "ext"


@pytest.fixture(scope="module")
def ref() -> dict:
    return json.loads((REF / "geostatspy_reference.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((REF / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_cross_checks_pass(manifest: dict) -> None:
    cc = manifest["cross_checks"]
    assert cc["nscore_backtr_roundtrip_max_abs"] < 1e-9
    assert abs(cc["declus_weights_sum_minus_n"]) < 1e-9
    for key, value in cc.items():
        if key.startswith("kb2d_"):
            assert max(value) < 1e-9, key
    assert len(manifest["geostatspy_commit"]) == 40
    assert manifest["known_defects"], "the defects found in GeostatsPy must stay recorded"


def test_dataset_shape(ref: dict) -> None:
    d = ref["data"]
    assert len(d["X"]) == len(d["Y"]) == len(d["V"]) == 80
    assert min(d["V"]) > 0


def test_nscore_is_monotone_standard_normal(ref: dict) -> None:
    ns = np.asarray(ref["nscore"]["ns"])
    assert abs(ns.mean()) < 0.05 and abs(ns.std() - 1.0) < 0.1
    order = np.argsort(ref["data"]["V"], kind="stable")
    assert np.all(np.diff(ns[order]) >= 0)  # ties get the same or rising score
    assert np.allclose(ref["nscore"]["backtr_roundtrip"], ref["data"]["V"], atol=1e-9)


def test_gamv_has_the_self_pair_in_lag_zero(ref: dict) -> None:
    for name, g in ref["gamv"].items():
        # GSLIB counts the self-pair at lag 0: once per direction, so twice for the omnidirectional variogram
        assert g["npairs"][0] == (160 if g["params"]["atol"] >= 90 else 80), name
        assert g["gamma"][0] == 0.0
        assert len(g["lag"]) == g["params"]["nlag"] + 2


def test_declus_weights_average_to_one(ref: dict) -> None:
    w = np.asarray(ref["declus"]["weights"])
    assert w.shape == (80,) and abs(w.sum() - 80) < 1e-9
    clustered, scattered = w[40:], w[:40]
    assert clustered.mean() < scattered.mean()  # the 40 points packed into 200x200 m weigh less


def test_kriging_point_invariants(ref: dict) -> None:
    cases = ref["kb2d"]["cases"]
    ok, sk = cases["ok_point_all"], cases["sk_point_all"]
    ok_var, sk_var = np.asarray(ok["var"]), np.asarray(sk["var"])
    assert np.all(ok_var >= -1e-12) and np.all(sk_var >= -1e-12)
    assert np.all(sk_var <= ok_var + 1e-9)  # SK knows the mean: it never has the larger variance
    sill = ref["kb2d"]["variogram"]["nug"] + ref["kb2d"]["variogram"]["cc1"]
    assert np.all(ok_var <= sill * 1.5)
    block = np.asarray(cases["ok_block4_all"]["var"])
    assert np.all(block <= ok_var + 1e-9)  # a block is smoother than a point: lower kriging variance


def test_omf_v1_sample_matches_expected() -> None:
    raw = (EXT / "omf" / "v1" / "test_file.omf").read_bytes()
    assert raw[:4] == bytes.fromhex("84838281") and raw[4:14] == b"OMF-v0.9.0"
    (offset,) = struct.unpack("<Q", raw[52:60])
    objs = json.loads(raw[offset:])
    project = next(v for v in objs.values() if v["__class__"] == "Project")
    exp = json.loads((EXT / "expected" / "test_file.v1.json").read_text(encoding="utf-8"))
    assert [objs[u]["name"] for u in project["elements"]] == [e["name"] for e in exp["elements"]]
    kinds = ["PointSetElement", "LineSetElement", "SurfaceElement", "SurfaceElement", "VolumeElement"]
    assert [e["class"] for e in exp["elements"]] == kinds


def test_omf2_samples_are_zip_and_not_v1() -> None:
    """BF reads v1 only: these two files exist to prove it refuses OMF 2 with a clear message (Э11)."""
    for rel, member in (("v2/test_file.omf", "project.json"), ("minelib/McLaughlin_tutorial.omf", "index.json.gz")):
        path = EXT / "omf" / rel
        assert path.read_bytes()[:2] == b"PK"
        with zipfile.ZipFile(path) as z:
            assert member in z.namelist()


def test_mclaughlin_block_count_matches_the_minelib_grid() -> None:
    with zipfile.ZipFile(EXT / "omf" / "minelib" / "McLaughlin_tutorial.omf") as z:
        idx = json.loads(gzip.decompress(z.read("index.json.gz")))
    bm = next(e for e in idx["elements"] if e["geometry"]["type"] == "BlockModel")
    count = bm["geometry"]["subblocks"]["subblocks"]["item_count"]
    assert count == 140 * 296 * 68  # MineLib McLaughlin, 7.62 x 7.62 x 6.096 m
    assert [a["name"] for a in bm["attributes"]] == ["au", "density", "rocktype", "hardness"]
