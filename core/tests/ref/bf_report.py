# ruff: noqa
# mypy: ignore-errors
"""BF `grade_tonnage` as the reference of Т8.10-2 (ADR 0027, finding 1; ADR 0022 «Перенос»).

TEST-ONLY reference code (never imported by bf_core, not part of the delivery). Source: the BF repository (branch
`claude/laughing-bohr-gpy965`, commit 453a756), `api/app/blocks/report.py`, copied as it is below this docstring.
BF is the code of the same owner as BedrockForge 2 (the project's own code, no third-party licence applies); the
notes of «Допущения» on the differences of BF2 from BF: cut-off `None` takes the blocks with no value of the cut-off
field too (BF2 does the same, the bin «нет g_б»), the metal unit is the unit of the template (decision 6, № 106).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

TROY_OUNCE_G = 31.1034768


@dataclass
class Grade:
    key: str
    name: str
    values: np.ndarray
    unit: str | None


def metal(sum_gt: float, unit: str | None) -> dict | None:
    """Metal in a tonnage at a grade: Σ(grade × tonnes) to grams / ounces or tonnes."""
    if unit in ("g/t", "ppm"):
        grams = sum_gt
        return {"value": grams / TROY_OUNCE_G / 1000.0, "unit": "тыс. унц", "kg": grams / 1000.0}
    if unit == "ppb":
        grams = sum_gt / 1000.0
        return {"value": grams / TROY_OUNCE_G / 1000.0, "unit": "тыс. унц", "kg": grams / 1000.0}
    if unit == "%":
        return {"value": sum_gt / 100.0, "unit": "т"}
    return None


def grade_tonnage(
    volume: np.ndarray,
    density: np.ndarray,
    grades: list[Grade],
    *,
    cutoff: np.ndarray | None,
    cutoffs: list[float | None],
    mask: np.ndarray,
    groups: tuple[np.ndarray, list[str]] | None = None,
) -> dict:
    tonnes = volume * density
    has_t = np.isfinite(tonnes) & (tonnes >= 0)
    base = mask & has_t
    rows: list[dict] = []
    group_list: list[tuple[str | None, np.ndarray]] = [(None, base)]
    if groups is not None:
        codes, labels = groups
        group_list += [(label, base & (codes == i)) for i, label in enumerate(labels)]
        if (codes < 0).any():
            group_list.append(("(пусто)", base & (codes < 0)))
    for label, in_group in group_list:
        if label is not None and not in_group.any():
            continue
        for c in cutoffs:
            sel = in_group if c is None or cutoff is None else in_group & (cutoff >= c)
            t = tonnes[sel]
            total_t = float(t.sum())
            row = {
                "group": label,
                "cutoff": c,
                "blocks": int(sel.sum()),
                "volume": float(volume[sel].sum()),
                "tonnes": total_t,
                "grades": {},
            }
            for g in grades:
                gv = g.values[sel]
                ok = np.isfinite(gv)
                w = t[ok]
                s = float((gv[ok] * w).sum())
                wt = float(w.sum())
                row["grades"][g.key] = {
                    "grade": s / wt if wt > 0 else None,
                    "metal": metal(s, g.unit),
                    "missing_tonnes": total_t - wt,
                }
            rows.append(row)
    return {
        "rows": rows,
        "excluded_no_density": int((mask & ~has_t).sum()),
    }
