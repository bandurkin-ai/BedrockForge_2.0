"""Batches of QAQC (ТЗ 8.4.2; ADR 0020 §4): a batch (LAB, BATCH) fails when a CRM or a blank of it fails. A pure
function of numpy over the columns of the records (after the filter, every type) and the statuses the kernels
of the standards and of the blanks gave; no numba, no database.

    batches(lab, batch, date, crm_rows, crm_status, blank_rows, blank_status) -> Batches
        lab    int[n]   the code of the laboratory of a record, ≥ 0 (the empty laboratory has a code of its own)
        batch  int[n]   the code of its batch; < 0 — the record has no batch
        date   as `standards.date_key` takes it: float64 milliseconds (NaN — none), datetime64, int64
        crm_rows int[c], crm_status int8[c]       the rows of the CRM samples among the records and their
                                                  statuses (`Standards.status`); the same for the blanks
    Batches — the batches by (the first date, laboratory, batch), a batch without dates last:
        lab, batch (codes), date_from, date_to float64 (the least and the greatest date of its records in the
        unit of `date`; NaN — none), n (records of every type), crm, blanks (control samples), crm_fail,
        blank_fail (those with the status `FAIL`), assessed (CRM and blanks that have a status),
        status int8 (`limits.FAIL` 2, `NORM` 0, `NOT_ASSESSED` −1)
        index int64[n] — the number of the batch of every record in this order, −1 — no batch
        total, failed, not_assessed (batches), no_batch (records without a batch)

Rules (ADR 0020 §4). The key is (LAB, BATCH): equal names of batches of two laboratories are two batches. A
batch fails when it holds a failed CRM (beyond k₃ or by the pair — the status 2 of the standards) or a failed
blank; a run on one side of μ is a warning and fails no batch. A batch none of whose CRM and blanks could be
assessed (no control samples, no reference values, no DL) is «не оценена» — not «норма». The records without a
batch are counted apart (`no_batch`) and belong to no batch, whatever their statuses.

One million records in 20 thousand batches — about 0.05 s, 60 MB.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .limits import FAIL, NORM, NOT_ASSESSED
from .standards import NO_DATE, codes_of, date_key, group_index

_LATE = int(np.iinfo(np.int64).max)


@dataclass(frozen=True)
class Batches:
    """The batches of the records (see the module docstring)."""

    lab: np.ndarray
    batch: np.ndarray
    date_from: np.ndarray
    date_to: np.ndarray
    n: np.ndarray
    crm: np.ndarray
    blanks: np.ndarray
    crm_fail: np.ndarray
    blank_fail: np.ndarray
    assessed: np.ndarray
    status: np.ndarray
    index: np.ndarray
    total: int
    failed: int
    not_assessed: int
    no_batch: int


def _samples(rows: Any, status: Any, n: int, name: str) -> tuple[np.ndarray, np.ndarray]:
    """The rows of the control samples of a kind among the records and their statuses, checked."""
    r, s = np.asarray(rows), np.asarray(status)
    if r.dtype.kind not in "iu" or s.dtype.kind not in "iu":
        raise TypeError(f"{name}_rows and {name}_status must be whole numbers")
    if r.ndim != 1 or s.shape != r.shape:
        raise ValueError(f"{name}_rows and {name}_status must be 1D of one length")
    if r.shape[0] and (int(r.min()) < 0 or int(r.max()) >= n):
        raise ValueError(f"{name}_rows must be rows of the records")
    return r.astype(np.int64, copy=False), s


def batches(
    lab: Any, batch: Any, date: Any, crm_rows: Any, crm_status: Any, blank_rows: Any, blank_status: Any
) -> Batches:
    """The batches of the records and their statuses by the CRM and the blanks (see the module docstring)."""
    bt = np.asarray(batch)
    if bt.dtype.kind not in "iu":
        raise TypeError("batch must be whole codes")
    if bt.ndim != 1:
        raise ValueError("batch must be 1D")
    n = int(bt.shape[0])
    lb = codes_of(lab, n, "lab")
    key = date_key(date, n)
    crm_r, crm_s = _samples(crm_rows, crm_status, n, "crm")
    blank_r, blank_s = _samples(blank_rows, blank_status, n, "blank")

    rows = np.flatnonzero(bt >= 0)
    of, (g_lab, g_batch) = group_index(lb[rows], bt[rows].astype(np.int64, copy=False))
    g = int(g_lab.shape[0])
    first, last = np.full(g, _LATE, dtype=np.int64), np.full(g, NO_DATE, dtype=np.int64)
    d = key[rows]
    dated = d != NO_DATE
    np.minimum.at(first, of[dated], d[dated])
    np.maximum.at(last, of[dated], d[dated])
    where = np.full(n, -1, dtype=np.int64)  # the batch of every record, by (laboratory, batch) so far
    where[rows] = of

    def count(at: np.ndarray) -> np.ndarray:
        return np.bincount(at[at >= 0], minlength=g).astype(np.int64, copy=False)

    crm_at, blank_at = where[crm_r], where[blank_r]
    crm_fail, blank_fail = count(crm_at[crm_s == FAIL]), count(blank_at[blank_s == FAIL])
    assessed = count(crm_at[crm_s != NOT_ASSESSED]) + count(blank_at[blank_s != NOT_ASSESSED])
    status = np.where(crm_fail + blank_fail > 0, FAIL, np.where(assessed > 0, NORM, NOT_ASSESSED)).astype(np.int8)

    by = np.argsort(first, kind="stable")  # equal first dates keep (laboratory, batch)
    place = np.empty(g, dtype=np.int64)
    place[by] = np.arange(g, dtype=np.int64)
    index = np.full(n, -1, dtype=np.int64)
    index[rows] = place[of]
    none = first[by] == _LATE
    return Batches(
        lab=g_lab[by],
        batch=g_batch[by],
        date_from=np.where(none, np.nan, first[by].astype(np.float64)),
        date_to=np.where(none, np.nan, last[by].astype(np.float64)),
        n=np.bincount(of, minlength=g).astype(np.int64, copy=False)[by],
        crm=count(crm_at)[by],
        blanks=count(blank_at)[by],
        crm_fail=crm_fail[by],
        blank_fail=blank_fail[by],
        assessed=assessed[by],
        status=status[by],
        index=index,
        total=g,
        failed=int(np.count_nonzero(status == FAIL)),
        not_assessed=int(np.count_nonzero(status == NOT_ASSESSED)),
        no_batch=n - int(rows.shape[0]),
    )
