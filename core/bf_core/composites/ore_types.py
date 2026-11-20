"""Ore intervals by the ГКЗ method (ТЗ 7.35 Д-26, ADR 0018 §8): what goes into the kernel and what comes out —
the parameters (`OreParams`), the samples (`OreLines`), the result (`OreIntervals`: interval rows and the
classes of the samples) and the protocol events (`OreEvents`). The codes are those of `ore_units`."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from . import ore_units as ou

# interval rows: integer and float columns
IV_HOLE, IV_LINE, IV_CLS, IV_BASIS, IV_N, IV_FLAG, IV_ORE_ID, IV_ROW0, IV_ROW1, IV_NESTED = range(10)
IV_I_COLS = 10
IV_FROM, IV_TO, IV_M, IV_MC, IV_WASTE = range(5)
IV_F_COLS = 5

MODE_TITLES = {
    "strict_excl": "Строгий, исключить пустую породу",
    "strict": "Строгий",
    "soft": "Смягчённый",
    "simple": "Упрощённый",
    "contacts": "По контактам",
}


class OreError(ValueError):
    """The calculation cannot run: {code, message, hint} in Russian, shown to the user."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


def _bad(message: str, hint: str = "Исправьте значение параметра.") -> OreError:
    return OreError("ore_bad_param", message, hint)


@dataclass(frozen=True)
class OreParams:
    """Кондиции and the mode (ТЗ 7.35 Д-26; names — as in the command `composite.ore_intervals`)."""

    cutoff: float  # Сб — бортовое содержание
    m_min: float  # Мрт — минимальная мощность рудного тела
    m_waste_max: float  # Мпп — максимальная мощность прослоя пустых пород
    mc_min: float | None = None  # МС — минимальный метропроцент; None — Сб·Мрт
    c_min: float | None = None  # Смин — минимальное содержание в интервале; None — выключен (= Сб)
    mode: str = "strict_excl"
    missing: str = "break"  # "break" — a missing value ends the line; "waste" — пустая порода до missing_max
    missing_max: float = 0.0  # L_н, m along the hole
    contacts_values: tuple[int, ...] | None = None  # «по контактам»: domain codes of the body; None — every code

    def checked(self) -> OreParams:
        """The parameters the kernel runs with: validated, МС and Смин filled in."""

        def number(value: Any, title: str, *, positive: bool = False) -> float:
            try:
                x = float(value)
            except (TypeError, ValueError):
                raise _bad(f"{title}: ожидается число.") from None
            if not np.isfinite(x) or x < 0.0 or (positive and x == 0.0):
                raise _bad(f"{title}: значение должно быть {'больше нуля' if positive else 'не меньше нуля'}.")
            return x

        cutoff = number(self.cutoff, "Бортовое содержание Сб", positive=True)
        m_min = number(self.m_min, "Минимальная мощность рудного тела Мрт")
        m_waste_max = number(self.m_waste_max, "Максимальная мощность пустого прослоя Мпп")
        mc_min = cutoff * m_min if self.mc_min is None else number(self.mc_min, "Минимальный метропроцент МС")
        c_min = cutoff if self.c_min is None else number(self.c_min, "Минимальное содержание Смин")
        if self.mode not in ou.MODES:
            raise _bad(f"Неизвестный режим «{self.mode}».", "Выберите один из пяти режимов выделения интервалов.")
        if self.missing not in ("break", "waste"):
            raise _bad(f"Пропуски: неизвестное значение «{self.missing}».", "Выберите «Разрыв» или «Пустая порода».")
        missing_max = number(self.missing_max, "Наибольший неопробованный промежуток")
        body = self.contacts_values
        if body is not None:
            try:
                body = tuple(sorted({int(v) for v in body}))
            except (TypeError, ValueError):
                raise _bad("Домены рудного тела: ожидается список кодов домена.") from None
            if any(v < 0 for v in body):
                raise _bad("Домены рудного тела: код домена не может быть отрицательным.")
        return replace(
            self, cutoff=cutoff, m_min=m_min, m_waste_max=m_waste_max, mc_min=mc_min, c_min=c_min,
            missing_max=missing_max, contacts_values=body,
        )  # fmt: skip


@dataclass(frozen=True)
class OreLines:
    """Samples of a set of holes ordered by (hole, FROM): rows `starts[h] : starts[h + 1]` belong to hole h."""

    starts: np.ndarray  # int64[holes + 1]
    frm: np.ndarray  # float64
    to: np.ndarray
    grade: np.ndarray  # NaN — no value
    thick: np.ndarray | None = None  # thickness of a sample; None — its length along the hole
    domain: np.ndarray | None = None  # int32 codes, −1 — outside the domains; None — no domains

    @staticmethod
    def of(
        starts: Any, frm: Any, to: Any, grade: Any, thick: Any | None = None, domain: Any | None = None
    ) -> OreLines:
        s = np.ascontiguousarray(starts, dtype=np.int64)
        cols = [np.ascontiguousarray(a, dtype=np.float64) for a in (frm, to, grade)]
        th = None if thick is None else np.ascontiguousarray(thick, dtype=np.float64)
        dm = None if domain is None else np.ascontiguousarray(domain, dtype=np.int32)
        n = len(cols[0])
        sizes = {len(a) for a in (*cols, *(x for x in (th, dm) if x is not None))}
        if s.ndim != 1 or len(s) < 1 or s[0] != 0 or s[-1] != n or np.any(np.diff(s) < 0) or sizes != {n}:
            raise OreError(
                "ore_bad_lines", "Пробы переданы ядру в неверном виде.", "Сообщите разработчику: это ошибка программы."
            )
        return OreLines(s, cols[0], cols[1], cols[2], th, dm)


@dataclass(frozen=True)
class OreEvents:
    """Protocol events of the kernel (ADR 0018 §9): `i[n, EI_COLS]`, `f[n, EF_COLS]` (`ore_units`)."""

    i: np.ndarray
    f: np.ndarray

    def __len__(self) -> int:
        return len(self.i)

    def records(self) -> list[dict[str, Any]]:
        """Events as dicts with names instead of codes (tests, the protocol of one hole)."""
        out = []
        for a, b in zip(self.i.tolist(), self.f.tolist(), strict=True):
            out.append(
                {
                    "hole": a[ou.EI_HOLE],
                    "line": a[ou.EI_LINE],
                    "step": a[ou.EI_STEP],
                    "stage": a[ou.EI_STAGE],
                    "kind": ou.KINDS[a[ou.EI_KIND]],
                    "side": ou.SIDES[a[ou.EI_SIDE]],
                    "ore": bool(a[ou.EI_ORE]),
                    "compared": ou.COMPARED[a[ou.EI_CMP]],
                    "decision": ou.DECISIONS[a[ou.EI_DEC]],
                    "row0": a[ou.EI_ROW0],
                    "row1": a[ou.EI_ROW1],
                    "seq": a[ou.EI_SEQ],
                    "from": b[ou.EF_FROM],
                    "to": b[ou.EF_TO],
                    "m": b[ou.EF_M],
                    "c": b[ou.EF_C],
                    "mc": b[ou.EF_MC],
                }
            )
        return out


@dataclass(frozen=True)
class OreIntervals:
    """Result of the kernel: interval rows and the classes of the samples. The rows with NESTED = 0 go in the
    order (hole, FROM) and cover every sampling line once; the nested rows (`waste_in`, `frame`; only when
    asked for) follow their `ore` row: its waste stretches from the top down, the frame above, the frame below."""

    params: OreParams
    iv_i: np.ndarray  # int64[rows, IV_I_COLS]
    iv_f: np.ndarray  # float64[rows, IV_F_COLS]
    sample_cls: np.ndarray  # int8 per sample: CLS_* of `ore_units`, −1 — outside the sampling lines
    sample_ore_id: np.ndarray  # int32 per sample: number of its ore interval in the hole (1…k), 0 — none
    sample_state: np.ndarray  # int8 per sample: ROW_* of `ore_units`
    stats: dict[str, int]
    events: OreEvents | None = None

    def __len__(self) -> int:
        return len(self.iv_i)

    @property
    def hole(self) -> np.ndarray:
        return self.iv_i[:, IV_HOLE]

    @property
    def line(self) -> np.ndarray:
        return self.iv_i[:, IV_LINE]

    @property
    def cls(self) -> np.ndarray:
        return self.iv_i[:, IV_CLS]

    @property
    def basis(self) -> np.ndarray:
        return self.iv_i[:, IV_BASIS]

    @property
    def n_samples(self) -> np.ndarray:
        return self.iv_i[:, IV_N]

    @property
    def flag(self) -> np.ndarray:
        """1 — the interval holds a missing stretch counted as waste («пустая порода до L_н»)."""
        return self.iv_i[:, IV_FLAG]

    @property
    def ore_id(self) -> np.ndarray:
        return self.iv_i[:, IV_ORE_ID]

    @property
    def row0(self) -> np.ndarray:
        """Row of the first sample of the interval (−1 — it has no samples)."""
        return self.iv_i[:, IV_ROW0]

    @property
    def row1(self) -> np.ndarray:
        """Row of the last sample of the interval."""
        return self.iv_i[:, IV_ROW1]

    @property
    def nested(self) -> np.ndarray:
        """1 — a `waste_in` or `frame` row: it lies over other rows and is not in the sums by class."""
        return self.iv_i[:, IV_NESTED]

    @property
    def frm(self) -> np.ndarray:
        return self.iv_f[:, IV_FROM]

    @property
    def to(self) -> np.ndarray:
        return self.iv_f[:, IV_TO]

    @property
    def m(self) -> np.ndarray:
        return self.iv_f[:, IV_M]

    @property
    def mc(self) -> np.ndarray:
        return self.iv_f[:, IV_MC]

    @property
    def c(self) -> np.ndarray:
        return self.iv_f[:, IV_MC] / self.iv_f[:, IV_M]

    @property
    def waste_len(self) -> np.ndarray:
        return self.iv_f[:, IV_WASTE]

    def rows(self) -> list[dict[str, Any]]:
        """Interval rows as dicts with class and basis names (tests, previews of one hole)."""
        out = []
        for a, b in zip(self.iv_i.tolist(), self.iv_f.tolist(), strict=True):
            out.append(
                {
                    "hole": a[IV_HOLE],
                    "line": a[IV_LINE],
                    "cls": ou.CLASSES[a[IV_CLS]],
                    "basis": ou.BASES[a[IV_BASIS]],
                    "n_samples": a[IV_N],
                    "flag": a[IV_FLAG],
                    "ore_id": a[IV_ORE_ID],
                    "row0": a[IV_ROW0],
                    "row1": a[IV_ROW1],
                    "nested": a[IV_NESTED],
                    "from": b[IV_FROM],
                    "to": b[IV_TO],
                    "m": b[IV_M],
                    "mc": b[IV_MC],
                    "c": b[IV_MC] / b[IV_M],
                    "waste_len": b[IV_WASTE],
                }
            )
        return out
