"""The XLSX book of a QAQC report (ТЗ 8.4.3; ADR 0020 §8, решение 15; подшаг 14.14) — `composites/ore_xlsx.Book`.

    write(path, recs, inputs, summary, images, tick=None, limit=MAX_LINES)

Sheets, in this order: «Сводка», «Стандарты», «Бланки», «Дубликаты», «Внешний контроль», «Партии», «Параметры». A
sheet of data: the table by groups (every element), an empty line, the table by samples (or pairs); the pictures of
its section — a column on the right of the tables. The tables by samples are computed again by the same kernels over
the records and inputs of the report (`calc.run`, `samples=True`): the numbers are those of the API in full float64
precision (a missing value — an empty cell, never 0); a sample is named by its SAMPLE_ID. A table longer than a
sheet (`limit` lines, 1 048 575 by default) goes on «‹имя› (2)», «(3)» … with its header again. Text that starts
like a formula gets «'» (`Book(guard=True)`). Statuses, the class of a bias and the seed — text; the lines of the
tables by groups are filled with the brand tones of their status («не оценён» — a group without a reference, without
assessed samples, a laboratory of blanks with nothing assessed); the tables by samples are not (a fill per cell
would need a line at a time — 12 s a million). The answers of an element go into numpy columns as soon as it is
computed (`gather`): the lists of one element live at a time, SAMPLE_ID becomes text a chunk at a time. «Партии»
also has the status of a batch by any element. «Параметры» (thresholds, types, DL, the CRM rows used, the filter,
hashes, versions, seed, time) goes last: the shared strings of the sheets before it do not depend on the name of the
data or the time, so the XML of the data sheets of a report by a filter and of one over the records taken by that
filter are equal byte for byte (Т8.4-8). «Дубликаты»: a line per element and type, the precision of
Thompson–Howarth, the pairs; «Внешний контроль»: a line per pair of laboratories, the pairs with their group.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from bf_core.composites.ore_xlsx import DATE, DATETIME, HEAD, MAX_ROWS, NOTE, Book, num_fmt, tone_style

from . import calc
from . import records as r

MAX_LINES = MAX_ROWS - 1
SHEETS = ("Сводка", "Стандарты", "Бланки", "Дубликаты", "Внешний контроль", "Партии", "Параметры")
STATUS = {0: "норма", 1: "предупреждение", 2: "отказ", -1: "не оценён"}
RULES = {1: "пара", 2: "серия", 4: "подмена"}
BIAS = {0: "норма", 1: "внимание", 2: "недопустимо"}  # the class of the bias of a CRM (5 and 10 %)
WARN_FILL = "#F4B13A"
DAY_MS = 86_400_000
PIC_GAP = 1
DUP_KINDS = ("DUP_F", "DUP_C", "DUP_P")
Tick = Callable[[float, str], None]


# --- the answers -----------------------------------------------------------------------------------------------------

_INTS = frozenset({"group", "row", "status", "rule"})
_TEXTS = frozenset({"batch"})


def _compact(samples: Mapping[str, Any] | None) -> dict[str, np.ndarray]:
    """The table by samples of an answer as numpy columns: the lists of the answer are dropped at once."""
    out: dict[str, np.ndarray] = {}
    for key, values in (samples or {}).items():
        if key in _INTS:
            out[key] = np.asarray(values, dtype=np.int64)
        elif key in _TEXTS:
            out[key] = np.asarray(values, dtype=object)
        elif key == "swap":
            out[key] = np.asarray([", ".join(x) if x else None for x in values], dtype=object)
        else:
            out[key] = np.array(values, dtype=np.float64)  # None → NaN: an empty cell
    return out


def _batch_columns(rows: Sequence[Mapping[str, Any]]) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {k: np.asarray([b[k] for b in rows], dtype=object) for k in ("lab", "batch")}
    for k in ("date_from", "date_to"):
        out[k] = _iso_dates([b[k] for b in rows])
    for k in ("n", "crm", "blanks", "crm_fail", "blank_fail", "assessed", "status"):
        out[k] = np.asarray([b[k] for b in rows], dtype=np.int64)
    return out


def gather(recs: Any, inputs: Mapping[str, Any], tick: Tick | None = None) -> dict[str, dict[str, Any]]:
    """Every section of every element of the report; the tables by samples (and of batches) of each element go
    into numpy columns as soon as it is computed — the lists of one element live at a time."""
    from .report import any_status

    out: dict[str, dict[str, Any]] = {}
    tables: list[list[dict[str, Any]]] = []
    elements = list(inputs["elements"])
    for i, element in enumerate(elements):
        if tick is not None:
            tick(i / max(len(elements), 1), f"Расчёт: {element}")
        recs.load(calc.columns_of(recs.schema, element))
        beat = None if tick is None else (lambda i=i, element=element: tick(i / max(len(elements), 1),
                                                                             f"Расчёт: {element}"))  # fmt: skip
        ctx = calc.context(recs, inputs["config"], inputs["crm"], element, inputs.get("filter"), tick=beat)
        part: dict[str, Any] = {"summary": calc.run(ctx, "summary")}
        runs: list[tuple[str, str, dict[str, Any]]] = [("standards", "standards", {}), ("blanks", "blanks", {})]
        runs += [(f"duplicates:{k}", "duplicates", {"kind": k}) for k in DUP_KINDS]
        runs.append(("external", "external", {}))
        for key, section, params in runs:
            answer = calc.run(ctx, section, samples=True, **params)
            answer["samples"] = _compact(answer.get("samples"))
            part[key] = answer
        rows = calc.run(ctx, "batches")["batches"]
        tables.append(rows)
        part["batches"] = _batch_columns(rows)
        out[element] = part
    status = any_status(tables)
    del tables
    for part in out.values():
        cols = part["batches"]
        cols["any"] = np.asarray([status[k] for k in zip(cols["lab"], cols["batch"], strict=True)], dtype=np.int64)
    return out


# --- cells --------------------------------------------------------------------------------------------------------


def _floats(values: Sequence[Any]) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in values], dtype=np.float64)


def _dates(ms: np.ndarray) -> np.ndarray:
    """ms since 1970 (NaN — no date) → datetime64: days when every date is midnight, else ms."""
    x = np.asarray(ms, dtype=np.float64)
    ok = ~np.isnan(x)
    whole = bool(np.all(np.mod(x[ok], DAY_MS) == 0))
    out = np.full(len(x), np.datetime64("NaT"), dtype="datetime64[ms]")
    out[ok] = x[ok].astype(np.int64).astype("datetime64[ms]")
    return out.astype("datetime64[D]") if whole else out


def _iso_dates(values: Sequence[Any]) -> np.ndarray:
    return np.array([np.datetime64(v) if v else np.datetime64("NaT") for v in values], dtype="datetime64[D]")


def _status(values: np.ndarray) -> np.ndarray:
    """The texts of statuses (−1, 0, 1, 2) — references to four strings, not a string per row."""
    names = np.array([STATUS[-1], STATUS[0], STATUS[1], STATUS[2]], dtype=object)
    return names[np.asarray(values, dtype=np.int64) + 1]  # type: ignore[no-any-return]


def _yes(value: Any) -> str | None:
    return None if value is None else ("да" if value else "нет")


def _rules(bits: np.ndarray) -> np.ndarray:
    names = np.array([", ".join(t for b, t in RULES.items() if k & b) or None for k in range(8)], dtype=object)
    return names[np.asarray(bits, dtype=np.int64) & 7]  # type: ignore[no-any-return]


def _join(parts: Sequence[np.ndarray], dtype: Any = None) -> np.ndarray:
    if not parts:
        return np.empty(0, dtype=dtype or object)
    return np.concatenate(parts) if dtype is None else np.concatenate(parts).astype(dtype)


class _Ids:
    """The SAMPLE_ID of rows as `Sheet.table` takes a column: sliced, turned into text a chunk at a time."""

    def __init__(self, ids: Any, rows: np.ndarray) -> None:
        self.ids, self.rows = ids, rows

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, part: slice) -> list[Any]:
        rows = self.rows[part]
        return self.ids.take(rows).to_pylist() if len(rows) else []  # type: ignore[no-any-return]


def _tone_of_standard(g: Mapping[str, Any]) -> int:
    if g["state"] != "ok" or g["norm"] + g["warn"] + g["fail"] == 0:
        return -1
    return 2 if g["fail"] else (1 if g["warn"] else 0)


def _tone_of_blanks(g: Mapping[str, Any]) -> int:
    if g["norm"] + g["fail"] == 0:
        return -1
    return 2 if g["fail"] else 0


def _bias_class(value: Any) -> str | None:
    return None if value is None else BIAS.get(int(value), str(value))


# --- sheets that go on ------------------------------------------------------------------------------------------


class _Sheets:
    """A sheet and its continuations «‹имя› (2)», …: a table longer than the room left goes on, its header again."""

    def __init__(self, book: Book, name: str, widths: Sequence[float], limit: int) -> None:
        self.book, self.name, self.widths, self.limit = book, name, [float(w) for w in widths], limit
        self.k = 1
        self.sh = book.sheet(name, widths=self.widths, freeze=0)

    def room(self) -> int:
        return self.limit - self.sh.rows

    def _next(self, header: Sequence[Any] | None) -> None:
        self.sh.close()
        self.k += 1
        self.sh = self.book.sheet(f"{self.name} ({self.k})", widths=self.widths, freeze=0)
        if header is not None:
            self.sh.row(list(header), style=HEAD)

    def row(self, values: Sequence[Any], style: Any = None, header: Sequence[Any] | None = None) -> None:
        if self.room() < 1:
            self._next(header)
        self.sh.row(list(values), style=style)

    def table(self, header: Sequence[Any], columns: Sequence[Any], style: Any = None) -> None:
        if self.room() < 2:
            self._next(None)
        self.sh.row(list(header), style=HEAD)
        n = len(columns[0]) if columns else 0
        a = 0
        while a < n:
            if self.room() < 1:
                self._next(header)
            take = min(self.room(), n - a)
            self.sh.table([c[a : a + take] for c in columns], style)
            a += take

    def images(self, pics: Sequence[bytes], col: int) -> None:
        line = 0
        for k, png in enumerate(pics):
            line = self.sh.image(png, line, col, scale=0.5, name=f"График {k + 1}") + PIC_GAP

    def close(self) -> None:
        self.sh.close()


# --- the book -----------------------------------------------------------------------------------------------------


class _Report:
    def __init__(self, book: Book, recs: Any, answers: dict[str, dict[str, Any]], limit: int) -> None:
        self.book, self.recs, self.answers, self.limit = book, recs, answers, limit
        self.ids = recs.ids(r.SAMPLE_ID)  # pyarrow: turned into text a chunk at a time (`_Ids`)
        self.date = book.style(fmt=DATE)
        self.stamp = book.style(fmt=DATETIME)
        self.pct = book.style(fmt=num_fmt(2, "units"))
        self.fills = {0: tone_style("pass"), 1: book.style(fill=WARN_FILL), 2: tone_style("reject"),
                      -1: tone_style("none")}  # fmt: skip

    def open(self, name: str, widths: Sequence[float], pics: Sequence[bytes], pic_col: int) -> _Sheets:
        out = _Sheets(self.book, name, widths, self.limit)
        out.images(pics, pic_col)
        return out

    # «Сводка»
    def summary(self, omitted: int) -> None:
        with self.book.sheet("Сводка", widths=[44, 16, 16, 16, 16, 16, 16], freeze=0) as sh:
            sh.row(["Сводка контроля качества анализов"], style=HEAD)
            for element, part in self.answers.items():
                s = part["summary"]
                sh.row([])
                sh.row([f"Элемент {element}" + (f", {s['unit']}" if s.get("unit") else "")], style=HEAD)
                c = s["counts"]
                sh.row(["Записей после фильтра", c["filtered"]])
                sh.row(["Контрольных записей", c["control"]])
                sh.row(["Не распознано по коду TYPE", c["unmapped"]])
                sh.row(["Контрольных без значения", c["missing"]])
                sh.row(["Записей по типам", *s["types"].keys()], style=[HEAD] * (len(s["types"]) + 1))
                sh.row(["", *s["types"].values()])
                st, bl, bt = s["standards"], s["blanks"], s["batches"]
                sh.row(["Стандарты: групп / проб", st["groups"], st["n"]])
                sh.row(["Стандарты: норма / предупреждений / отказов", st["norm"], st["warn"], st["fail"]])
                sh.row(["Стандарты: пар / серий / подмен", st["pair_fails"], st["run_flags"], st["swaps"]])
                sh.row(["Бланки: проб / норма / отказов / не оценено", bl["n"], bl["norm"], bl["fail"],
                        bl["not_assessed"]])  # fmt: skip
                sh.row(["Партии: всего / с отказом / не оценено / без партии", bt["total"], bt["failed"],
                        bt["not_assessed"], bt["no_batch"]])  # fmt: skip
                for kind, d in (s.get("duplicates") or {}).items():
                    sh.row([f"Дубликаты {kind}: пар / взято / в пределах порога, % / пройден", d.get("pairs"),
                            d.get("used"), d.get("share_at_limit"), _yes(d.get("passed"))])  # fmt: skip
                ext = s.get("external") or {}
                if ext:
                    sh.row(["Внешний контроль: групп / пар / взято / пройдено / не пройдено", ext.get("groups"),
                            ext.get("pairs"), ext.get("used"), ext.get("passed"), ext.get("failed")])  # fmt: skip
                for u in s["unmapped_codes"]:
                    sh.row(["Код не распознан", u["code"], u["n"]])
                for note in s["notes"]:
                    sh.row([note], style=NOTE)
            if omitted:
                sh.row([])
                sh.row([f"Графиков сверх {200}: {omitted} — в отчёт не вошли."], style=NOTE)

    # «Стандарты»
    def standards(self, pics: Sequence[bytes]) -> None:
        head = ["Элемент", "CRM", "Лаборатория", "Состояние", "Проб", "Пропусков", "μ", "σ", "Единица", "Среднее",
                "СКО", "Смещение, %", "Класс смещения", "Норма", "Предупреждений", "Отказов", "Пар", "Серий",
                "Подмен"]  # fmt: skip
        sh = self.open("Стандарты", [10, 14, 12, 14] + [12] * 15, pics, len(head) + 1)
        sh.row(head, style=HEAD)
        for element, part in self.answers.items():
            for g in part["standards"]["groups"]:
                tone = _tone_of_standard(g)
                st = [self.fills[tone]] * len(head)
                st[11] = self.book.style(fmt=num_fmt(2, "units"), fill=None, base=self.fills[tone])
                sh.row([element, g["crm"], g["lab"], g["state"], g["n"], g["missing"], g["mu"], g["sd"], g["unit"],
                        g["mean"], g["std"], g["bias"], _bias_class(g["bias_class"]), g["norm"], g["warn"], g["fail"],
                        g["pair_fails"], g["run_flags"], g["swaps"]], style=st)  # fmt: skip
        sh.row([])
        cols: list[list[np.ndarray]] = [[] for _ in range(13)]
        for element, part in self.answers.items():
            a = part["standards"]
            s, groups = a["samples"], a["groups"]
            if "group" not in s:
                continue
            g = s["group"]
            cols[0].append(np.full(len(g), element, dtype=object))
            cols[1].append(np.asarray([x["crm"] for x in groups], dtype=object)[g])
            cols[2].append(np.asarray([x["lab"] for x in groups], dtype=object)[g])
            cols[3].append(s["row"])
            cols[4].append(s["date"])
            for j, key in enumerate(("seq", "batch", "x", "z"), start=5):
                cols[j].append(s[key])
            cols[9].append(_status(s["status"]))
            cols[10].append(_rules(s["rule"]))
            cols[11].append(s["ma"])
            cols[12].append(s["swap"])
        header = ["Элемент", "CRM", "Лаборатория", "SAMPLE_ID", "Дата", "SEQ", "Партия", "Значение", "z", "Статус",
                  "Правило", "Скользящее среднее", "Подмена на"]  # fmt: skip
        data: list[Any] = [_join(c) for c in cols]
        data[3] = _Ids(self.ids, _join(cols[3], np.int64))
        data[4] = _dates(_join(cols[4], np.float64))
        sh.table(header, data)
        sh.close()

    # «Бланки»
    def blanks(self, pics: Sequence[bytes]) -> None:
        head = ["Элемент", "Лаборатория", "Проб", "Пропусков", "DL", "Порог", "Разные DL", "Норма", "Отказов",
                "Не оценено", "Пар переноса", "r переноса"]  # fmt: skip
        sh = self.open("Бланки", [10, 12] + [12] * 10, pics, len(head) + 1)
        sh.row(head, style=HEAD)
        for element, part in self.answers.items():
            for g in part["blanks"]["groups"]:
                c = g.get("carryover") or {}
                sh.row([element, g["lab"], g["n"], g["missing"], g["dl"], g["limit"], "да" if g["mixed"] else "нет",
                        g["norm"], g["fail"], g["not_assessed"], c.get("n"), c.get("pearson")],
                       style=[self.fills[_tone_of_blanks(g)]] * len(head))  # fmt: skip
        sh.row([])
        cols: list[list[np.ndarray]] = [[] for _ in range(10)]
        for element, part in self.answers.items():
            a = part["blanks"]
            s, groups = a["samples"], a["groups"]
            if "group" not in s:
                continue
            g = s["group"]
            cols[0].append(np.full(len(g), element, dtype=object))
            cols[1].append(np.asarray([x["lab"] for x in groups], dtype=object)[g])
            for j, key in enumerate(("row", "date", "seq", "batch", "x", "status", "prev", "limit"), start=2):
                cols[j].append(_status(s[key]) if key == "status" else s[key])
        header = ["Элемент", "Лаборатория", "SAMPLE_ID", "Дата", "SEQ", "Партия", "Значение", "Статус",
                  "Предыдущая проба", "Порог"]  # fmt: skip
        data: list[Any] = [_join(c) for c in cols]
        data[2] = _Ids(self.ids, _join(cols[2], np.int64))
        data[3] = _dates(_join(cols[3], np.float64))
        sh.table(header, data)
        sh.close()

    # «Дубликаты»
    def duplicates(self, pics: Sequence[bytes]) -> None:
        head = ["Элемент", "Тип", "Пар", "Взято", "Без исходной", "Пропуск", "Отрицательные", "Нулевые", "Ниже k·DL",
                "Порог HARD, %", "Доля в пределах, %", "≤ 10 %", "≤ 20 %", "≤ 30 %", "В пределах порога", "Пройден",
                "Среднее исходных", "Среднее дубликатов", "r Пирсона", "RMA a", "RMA b", "Т-Х", "Т-Х a", "Т-Х b",
                "Т-Х K"]  # fmt: skip
        sh = self.open("Дубликаты", [10, 8] + [12] * (len(head) - 2), pics, len(head) + 1)
        sh.row(head, style=HEAD)
        found = [(e, k.split(":", 1)[1], a) for e, part in self.answers.items() for k, a in part.items()
                 if k.startswith("duplicates:")]  # fmt: skip
        for element, kind, a in found:
            ex, st, th = a.get("excluded") or {}, a.get("stats") or {}, a.get("th") or {}
            rma = st.get("rma") or {}
            tone = -1 if a.get("passed") is None else (0 if a["passed"] else 2)
            sh.row([element, kind, a.get("pairs"), a.get("used"), *(ex.get(k) for k in ("no_parent", "missing",
                    "negative", "zero", "below")), a.get("hard_limit"), a.get("share_limit"), a.get("share10"),
                    a.get("share20"), a.get("share30"), a.get("share_at_limit"), _yes(a.get("passed")),
                    st.get("mean1"), st.get("mean2"), st.get("pearson"), rma.get("a"), rma.get("b"), th.get("state"),
                    th.get("a"), th.get("b"), th.get("k")], style=[self.fills[tone]] * len(head))  # fmt: skip
        sh.row([])
        sh.row(["Элемент", "Тип", "Содержание c", "Перцентиль", "P(c), %"], style=HEAD)
        for element, kind, a in found:
            for pc in (a.get("th") or {}).get("precision") or []:
                sh.row([element, kind, pc.get("c"), pc.get("q"), pc.get("p")])
        sh.row([])
        cols: list[list[np.ndarray]] = [[] for _ in range(9)]
        for element, kind, a in found:
            s = a["samples"]
            if "row" not in s:
                continue
            n = len(s["row"])
            cols[0].append(np.full(n, element, dtype=object))
            cols[1].append(np.full(n, kind, dtype=object))
            for j, key in enumerate(("row", "x1", "x2", "mean", "hard", "hrd", "ampd"), start=2):
                cols[j].append(s[key])
        header = ["Элемент", "Тип", "SAMPLE_ID", "Исходная", "Дубликат", "Среднее", "HARD, %", "HRD, %", "AMPD, %"]
        data: list[Any] = [_join(c) for c in cols]
        data[2] = _Ids(self.ids, _join(cols[2], np.int64))
        sh.table(header, data)
        sh.close()

    # «Внешний контроль»
    def external(self, pics: Sequence[bytes]) -> None:
        head = ["Элемент", "Основная", "Контрольная", "Пар", "Взято", "Среднее основной", "Среднее контрольной",
                "Смещение, %", "t", "p", "df", "r Пирсона", "RMA a", "RMA b", "a: от", "a: до", "b: от", "b: до",
                "Реализаций", "Зерно", "≤ 10 %", "≤ 20 %", "≤ 30 %", "В пределах порога", "Пройден",
                "Примечания"]  # fmt: skip
        sh = self.open("Внешний контроль", [10, 12, 12] + [12] * (len(head) - 3), pics, len(head) + 1)
        sh.row(head, style=HEAD)
        for element, part in self.answers.items():
            for g in part["external"].get("groups", []):
                rma = g.get("rma") or {}
                a_ci, b_ci = rma.get("a_ci") or [None, None], rma.get("b_ci") or [None, None]
                tone = -1 if g.get("passed") is None else (0 if g["passed"] else 2)
                st = [self.fills[tone]] * len(head)
                st[7] = self.book.style(fmt=num_fmt(2, "units"), base=self.fills[tone])
                seed = rma.get("seed")
                sh.row([element, g.get("lab_main"), g.get("lab_check"), g.get("pairs"), g.get("used"),
                        g.get("mean_main"), g.get("mean_check"), g.get("bias"), g.get("t"), g.get("p"), g.get("df"),
                        g.get("pearson"), rma.get("a"), rma.get("b"), a_ci[0], a_ci[1], b_ci[0], b_ci[1],
                        rma.get("n_boot"), None if seed is None else str(seed), g.get("share10"), g.get("share20"),
                        g.get("share30"), g.get("share_at_limit"), _yes(g.get("passed")),
                        "; ".join(g.get("notes") or []) or None], style=st)  # fmt: skip
        sh.row([])
        cols: list[list[np.ndarray]] = [[] for _ in range(10)]
        for element, part in self.answers.items():
            a = part["external"]
            s, groups = a["samples"], a.get("groups", [])
            if "group" not in s:
                continue
            g = s["group"]
            cols[0].append(np.full(len(g), element, dtype=object))
            cols[1].append(np.asarray([x.get("lab_main") for x in groups], dtype=object)[g])
            cols[2].append(np.asarray([x.get("lab_check") for x in groups], dtype=object)[g])
            for j, key in enumerate(("row", "x1", "x2", "mean", "hard", "hrd", "ampd"), start=3):
                cols[j].append(s[key])
        header = ["Элемент", "Основная", "Контрольная", "SAMPLE_ID", "Основная лаборатория", "Контрольная лаборатория",
                  "Среднее", "HARD, %", "HRD, %", "AMPD, %"]  # fmt: skip
        data: list[Any] = [_join(c) for c in cols]
        data[3] = _Ids(self.ids, _join(cols[3], np.int64))
        sh.table(header, data)
        sh.close()

    # «Партии»
    def batches(self) -> None:
        sh = _Sheets(self.book, "Партии", [10, 12, 12, 12, 12] + [10] * 8, self.limit)
        keys = ("lab", "batch", "date_from", "date_to", "n", "crm", "blanks", "crm_fail", "blank_fail", "assessed")
        cols: list[list[np.ndarray]] = [[] for _ in range(len(keys) + 3)]
        for element, part in self.answers.items():
            b = part["batches"]
            cols[0].append(np.full(len(b["lab"]), element, dtype=object))
            for j, key in enumerate(keys, start=1):
                cols[j].append(b[key])
            cols[-2].append(_status(b["status"]))
            cols[-1].append(_status(b["any"]))
        header = ["Элемент", "Лаборатория", "Партия", "Первая дата", "Последняя дата", "Проб", "CRM", "Бланков",
                  "Отказов CRM", "Отказов бланков", "Оценено", "Статус", "Статус по любому элементу"]  # fmt: skip
        data: list[Any] = [_join(c) for c in cols]
        for j in (3, 4):
            data[j] = _join(cols[j], "datetime64[D]")
        for j in range(5, 11):
            data[j] = _join(cols[j], np.int64)
        sh.table(header, data)
        sh.close()

    # «Параметры»
    def parameters(self, inputs: Mapping[str, Any], meta: Mapping[str, Any]) -> None:
        with self.book.sheet("Параметры", widths=[34, 22, 16, 16, 16, 30], freeze=0) as sh:
            sh.row(["Параметры отчёта"], style=HEAD)
            for label, value in (
                ("Данные", inputs.get("data_name")),
                ("SHA-256 записей", inputs.get("data_sha256")),
                ("SHA-256 входов", meta.get("inputs_sha256")),
                ("Алгоритм", inputs.get("algo")),
                ("Построен", inputs.get("created_at")),
                ("Элементы", ", ".join(inputs["elements"])),
                ("Фильтр", json.dumps(inputs.get("filter"), ensure_ascii=False)),
                ("Зерно", str(inputs["config"]["limits"].get("seed"))),
                ("Пустой код — ORIG", "да" if inputs["config"].get("empty_is_orig") else "нет"),
            ):
                sh.row([label, value])  # fmt: skip
            for lib, version in (inputs.get("versions") or {}).items():
                sh.row([f"Версия {lib}", version])
            sh.row([])
            sh.row(["Порог", "Значение"], style=HEAD)
            for k, v in inputs["config"]["limits"].items():
                sh.row([k, str(v) if k == "seed" else v])  # a seed past 2**53 is no float of a cell
            sh.row([])
            sh.row(["Код TYPE", "Тип"], style=HEAD)
            for code, kind in sorted(inputs["config"]["types"].items()):
                sh.row([code, kind])
            sh.row([])
            sh.row(["Элемент", "Лаборатория", "DL", "Единица"], style=HEAD)
            for d in inputs["config"].get("dl", []):
                sh.row([d["element"], d.get("lab") or "любая", d["value"], d.get("unit")])
            sh.row([])
            used = {(str(g["crm"]).casefold(), e.casefold()) for e, part in self.answers.items()
                    for g in part["standards"]["groups"]}  # fmt: skip
            sh.row(["CRM_ID", "Элемент", "μ", "σ", "Единица", "Источник"], style=HEAD)
            for c in inputs["crm"]:
                if (str(c["crm_id"]).casefold(), str(c["element"]).casefold()) in used:
                    sh.row([c["crm_id"], c["element"], c["mean"], c["sd"], c.get("unit"), c.get("source")])


def write(
    path: Path,
    recs: Any,
    inputs: Mapping[str, Any],
    meta: Mapping[str, Any],
    images: Mapping[str, bytes],
    charts: Sequence[Mapping[str, Any]],
    omitted: int = 0,
    tick: Tick | None = None,
    limit: int = MAX_LINES,
) -> None:
    """The book of the report (see the module docstring); `images` — PNG by the keys of `charts`."""
    answers = gather(recs, inputs, (lambda f, m: tick(0.6 * f, m)) if tick else None)

    def pics(section: str) -> list[bytes]:
        return [images[c["key"]] for c in charts if c["section"] == section and c["key"] in images]

    with Book(path, guard=True) as book:
        rep = _Report(book, recs, answers, limit)
        steps: list[tuple[str, Callable[[], None]]] = [
            ("Сводка", lambda: rep.summary(omitted)),
            ("Стандарты", lambda: rep.standards(pics("standards"))),
            ("Бланки", lambda: rep.blanks(pics("blanks"))),
            ("Дубликаты", lambda: rep.duplicates(pics("duplicates"))),
            ("Внешний контроль", lambda: rep.external(pics("external"))),
            ("Партии", rep.batches),
            ("Параметры", lambda: rep.parameters(inputs, meta)),
        ]
        for k, (title, step) in enumerate(steps):
            if tick is not None:
                tick(0.6 + 0.4 * k / len(steps), f"Лист «{title}»")
            step()
