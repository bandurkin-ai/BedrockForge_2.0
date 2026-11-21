"""Synthetic QAQC control records (ADR 0020 «Данные тестов», вопрос № 38) for pytest, benchmarks and E2E.

    gen = generate(control=100_000, seed=12345)      # one seed — one table, byte for byte
    gen.table                                         # pyarrow Table of text cells, as in a file
    gen.crm, gen.dl, gen.elements                     # the CRM reference rows, DL rows, (name, unit, column)
    write_csv(path, gen)                              # CSV UTF-8, «,», header — bytes depend only on the seed

Composition of the control records (shares of `control`): 30 % CRM (`crm` standards over `labs` laboratories),
15 % blanks, 15 % each of DUP_F, DUP_C, DUP_P, 10 % UMP; every duplicate and UMP has its own ORIG parent (so the
file holds ≈ 1.55 × `control` rows). Records are shuffled into batches of `batch_size` of the main laboratories
(UMP — of the laboratory «EXT»), SEQ 1… inside a batch, one date per batch. Values: ORIG lognormal; duplicates —
the parent × (1 + N(0, 15 / 8 / 4 %)); UMP — × (1 + N(2 %, 5 %)); CRM — N(μ, σ) with `fail` of them shifted by
±4σ; blanks — «<DL» mostly, some at 0.5–3 DL, `fail` of them past 5 DL; values below DL are written «<DL»;
`missing` of the cells are empty. Values are rounded as a laboratory reports them (4 significant digits).

CLI: `python -m tools.gen.qaqc --control 100000 --seed 12345 --out qaqc.csv`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

ELEMENTS = (("AU", "ppm", -0.5, 1.0, 0.01), ("CU", "%", -1.0, 0.8, 0.001), ("AG", "ppm", 1.0, 1.0, 0.5))
SHARES = {"CRM": 0.30, "BLANK": 0.15, "DUP_F": 0.15, "DUP_C": 0.15, "DUP_P": 0.15, "UMP": 0.10}
REL = {"DUP_F": 0.15, "DUP_C": 0.08, "DUP_P": 0.04}
COLUMNS = ("SAMPLE_ID", "HOLE", "TYPE", "PARENT_ID", "CRM_ID", "LAB", "BATCH", "DATE", "SEQ")
DAY_MS = 86_400_000
START_MS = 1_704_067_200_000  # 2024-01-01


@dataclass
class Generated:
    table: Any  # pyarrow.Table of strings (null — an empty cell)
    crm: list[dict[str, Any]] = field(default_factory=list)
    dl: list[dict[str, Any]] = field(default_factory=list)
    elements: list[tuple[str, str, str]] = field(default_factory=list)  # (name, unit, column)
    counts: dict[str, int] = field(default_factory=dict)


def _round(values: np.ndarray, digits: int = 4) -> np.ndarray:
    """Round to `digits` significant digits (what a laboratory reports)."""
    out = values.copy()
    ok = np.isfinite(out) & (out != 0)
    mag = np.floor(np.log10(np.abs(out[ok])))
    scale = 10.0 ** (digits - 1 - mag)
    out[ok] = np.round(out[ok] * scale) / scale
    return out


def _cells(values: np.ndarray, dl: float, empty: np.ndarray) -> Any:
    import pyarrow as pa
    import pyarrow.compute as pc

    text = pc.cast(pa.array(_round(values)), pa.string())
    below = pa.array(values < dl)
    text = pc.if_else(below, pa.scalar(f"<{dl:g}"), text)
    return pc.if_else(pa.array(empty), pa.scalar(None, pa.string()), text)


def generate(
    control: int = 10_000,
    *,
    crm: int = 20,
    labs: int = 3,
    batch_size: int = 50,
    seed: int = 12345,
    fail: float = 0.01,
    missing: float = 0.002,
    crm_code: str = "CRM",
) -> Generated:
    import pyarrow as pa

    rng = np.random.default_rng(seed)
    kinds = list(SHARES)
    sizes = [int(round(control * SHARES[k])) for k in kinds]
    sizes[0] += control - sum(sizes)
    n_par = sum(s for k, s in zip(kinds, sizes, strict=True) if k.startswith("DUP") or k == "UMP")
    kind = np.concatenate([np.full(s, i, np.int8) for i, s in enumerate(sizes)] + [np.full(n_par, -1, np.int8)])
    n = len(kind)
    is_par = kind == -1
    linked = np.flatnonzero(kind >= 2)  # DUP_F, DUP_C, DUP_P, UMP: each has one parent
    parents = np.flatnonzero(is_par)
    parent_of = np.full(n, -1, np.int64)
    parent_of[linked] = parents
    ump = kind == kinds.index("UMP")
    # batches: the main laboratories take all but UMP, «EXT» takes UMP
    main = np.flatnonzero(~ump)
    ext = np.flatnonzero(ump)
    order = np.concatenate([main[rng.permutation(len(main))], ext[rng.permutation(len(ext))]])
    pos = np.empty(n, np.int64)
    pos[order] = np.arange(n)
    n_main_b = -(-len(main) // batch_size)
    batch = np.where(pos < len(main), pos // batch_size, n_main_b + (pos - len(main)) // batch_size)
    seq = np.where(pos < len(main), pos % batch_size, (pos - len(main)) % batch_size) + 1
    n_batches = int(batch.max()) + 1 if n else 0
    batch_lab = rng.integers(0, labs, n_batches)
    lab_names = np.array([f"LAB{i + 1}" for i in range(labs)] + ["EXT"])
    lab = np.where(ump, labs, batch_lab[batch])
    batch_day = np.sort(rng.integers(0, 365, n_batches))
    date_ms = START_MS + batch_day[batch].astype(np.int64) * DAY_MS
    crm_of = np.where(kind == 0, rng.integers(0, crm, n), -1)
    hole = np.where(is_par, rng.integers(1, 201, n), 0)
    hole[linked] = hole[parents]

    cols: dict[str, Any] = {}
    gen = Generated(None)
    for name, unit, mu, sd, dl in ELEMENTS:
        orig = rng.lognormal(mu, sd, n)
        v = orig.copy()
        for k, rel in REL.items():
            sel = kind == kinds.index(k)
            v[sel] = np.abs(orig[parent_of[sel]] * (1 + rng.normal(0, rel, int(sel.sum()))))
        v[ump] = np.abs(orig[parent_of[ump]] * (1 + rng.normal(0.02, 0.05, int(ump.sum()))))
        means = _round(np.exp(mu) * np.geomspace(0.3, 10, crm))
        sds = _round(means * 0.04, 3)
        sel = kind == 0
        v[sel] = rng.normal(means[crm_of[sel]], sds[crm_of[sel]])
        shift = sel & (rng.random(n) < fail)
        v[shift] += np.where(rng.random(n) < 0.5, -4, 4)[shift] * sds[np.maximum(crm_of, 0)][shift]
        blank = kind == kinds.index("BLANK")
        v[blank] = np.where(rng.random(n) < 0.7, dl * 0.4, dl * rng.uniform(0.5, 3, n))[blank]
        hot = blank & (rng.random(n) < fail)
        v[hot] = dl * rng.uniform(6, 20, n)[hot]
        column = f"{name}_{unit}"
        cols[column] = _cells(v, dl, rng.random(n) < missing)
        gen.elements.append((name, unit, column))
        gen.dl.append({"element": name, "lab": None, "value": dl, "unit": unit})
        gen.crm += [
            {"crm_id": f"CRM-{i + 1:02d}", "element": name, "mean": float(means[i]), "sd": float(sds[i]),
             "unit": unit, "source": "генератор"}
            for i in range(crm)
        ]  # fmt: skip

    def text(values: Any, empty: np.ndarray) -> Any:
        return pa.array(np.where(empty, None, values).tolist(), type=pa.string())

    ids = np.char.add("S", np.char.zfill(np.arange(1, n + 1).astype(str), 7))
    type_names = np.array([crm_code if k == "CRM" else k for k in kinds] + ["ORIG"])
    base = {
        "SAMPLE_ID": pa.array(ids.tolist(), type=pa.string()),
        "HOLE": text(np.char.add("DH", np.char.zfill(hole.astype(str), 3)), hole == 0),
        "TYPE": pa.array(type_names[kind].tolist(), type=pa.string()),
        "PARENT_ID": text(ids[np.maximum(parent_of, 0)], parent_of < 0),
        "CRM_ID": text(np.char.add("CRM-", np.char.zfill((crm_of + 1).astype(str), 2)), crm_of < 0),
        "LAB": pa.array(lab_names[lab].tolist(), type=pa.string()),
        "BATCH": pa.array(np.char.add("B", np.char.zfill(batch.astype(str), 6)).tolist(), type=pa.string()),
        "DATE": pa.array(date_ms.astype("datetime64[ms]").astype("datetime64[D]").astype(str).tolist()),
        "SEQ": pa.array(seq.astype(str).tolist(), type=pa.string()),
    }
    out = pa.table({**base, **cols}).take(pa.array(np.lexsort((seq, batch))))
    gen.table = out
    gen.counts = {"rows": n, "control": control, "parents": n_par, "batches": n_batches}
    return gen


def write_csv(path: str | Path, gen: Generated) -> int:
    import pyarrow.csv as pacsv

    pacsv.write_csv(gen.table, str(path), pacsv.WriteOptions(quoting_style="needed"))
    return int(gen.table.num_rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic QAQC control records (CSV)")
    ap.add_argument("--control", type=int, default=100_000)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    print(f"{a.out}: {write_csv(a.out, generate(a.control, seed=a.seed))} rows")


if __name__ == "__main__":
    main()
