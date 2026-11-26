"""Parameters of an implicit model (подшаг 19.1; ТЗ 8.9.2–8.9.4; 7.35 Д-24, Д-25, Д-31, Д-39; ADR 0026 решения
4–22, 30): the body of `POST /implicit/models`, `PUT /implicit/models/{id}`, of a group and of the requests of the
model, checked against the ranges of 8.9.4 (docs/progress/e19-api.md §3–5). Pure: no database, no numpy (the
router loads it); ids, tables and fields are checked against the project by `store.py` (19.11). The parts and
the constants — `spec_parts.py` (re-exported here).

    spec = parse_model(body)          # ModelSpec; ApiError 422 `bad_param` {param}
    spec = spec.resolved(extent)      # h = diagonal / 200 when not given (the extent of the boundary is known)
    meta_part = spec.canonical()      # what `objects(kind="implicit_model").meta` keeps besides META_ONLY
    g = parse_group(raw, "group")     # one group (POST/PUT …/groups); spec.with_group(g) checks it in the model
    s = spec.s_of(g); e = eta(spec.kernel, s, h_bar)      # η of 8.9.2
    b = parse_build(body); p = parse_preview(body); c = parse_check(q); …
    raise refuse("no_contacts", group=3)                  # the refusals of решение 30 with their Russian texts

A key the body leaves out (or null, or "") takes its default (8.9.4): linear kernel, h = diagonal of the extent / 200
(clamped to 0,1–100 m), L = 2, N_уч / N_min / δ = 1000 / 100 / 0,25, D_max / Δ_т = 30 / 5 м, s = 0 for contacts and
0,05 for grades (`s` null — by the type of the group), «Авто» cond ≤ 1e8, p = 0,5 of an indicator shell. A key
the body does not know is ignored (the stored `meta` parses as it is). `param` of an error is the path of the key:
"h", "pou.n_min", "groups.2.codes.0", "groups.1.params.thresholds.3", "anisotropy.ratios.2", "boundary.box.4".
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from bf_core.geostat.estimate.spec_parts import id_of, list_of, obj, text_of
from bf_core.geostat.variogram.spec import bad, choice

from .spec_parts import (
    ALGO,
    CELLS_AXIS_MAX,
    CHECK_SORTS,
    CLIPS,
    CODES_MAX,
    COND_TARGET,
    D_MAX,
    DELTA,
    ETA_POWER,
    FORMATS,
    GROUP_TYPES,
    GROUPS_MAX,
    H_DIVISOR,
    KERNELS,
    LEVELS,
    MANUAL_MAX,
    META_ONLY,
    N_MIN,
    N_PATCH,
    OUTPUT_KINDS,
    P_INDICATOR,
    PAGE_DEFAULT,
    POINT_TYPES,
    POINTS_MAX,
    PREVIEW_DEFAULT,
    PREVIEW_MAX,
    RANGES,
    REFUSALS,
    RELATIONS,
    ROLES,
    S_CONTACT,
    S_GRADE,
    SHELL_MODES,
    SHELL_SOURCES,
    STAGES,
    STATES,
    STEP,
    SUPPORT_FACTOR,
    THRESHOLDS_MAX,
    TRANSFORMS,
    TREND_RADIUS,
    VEIN_BOUNDS,
    VEINS_MAX,
    Anisotropy,
    Boundary,
    DataSel,
    Group,
    Manual,
    Post,
    Pou,
    Smoothing,
    _ids,
    _int,
    _num,
    eta,
    parse_anisotropy,
    parse_boundary,
    parse_data,
    parse_group,
    parse_manual,
    parse_post,
    parse_pou,
    parse_smoothing,
    ranged,
    refuse,
    resolve_h,
)

__all__ = ["ALGO", "ETA_POWER", "GROUP_TYPES", "KERNELS", "OUTPUT_KINDS", "POINT_TYPES", "RANGES", "REFUSALS",
           "RELATIONS", "STAGES", "STATES", "Anisotropy", "BuildSpec", "Boundary", "CheckSpec", "CompareSpec",
           "DataSel", "Group", "Manual", "ModelSpec", "Post", "Pou", "PreviewSpec", "Smoothing", "defaults_json",
           "eta", "parse_build", "parse_check", "parse_compare", "parse_group", "parse_model", "parse_preview",
           "parse_relations", "parse_support", "ranged", "ranges_json", "refuse", "resolve_h"]  # fmt: skip


# --- the model ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelSpec:
    name: str | None  # None — the store gives «Неявная модель» (занято — « (2)»)
    boundary: Boundary
    h: float | None  # None until `resolved` (diagonal / 200)
    levels: int
    kernel: str
    kernel_radius: float | None  # wendland: a; None — 2·R of each patch
    smoothing: Smoothing
    trend_id: int | None  # object_refs role "trend"; global or structural (Д-15)
    anisotropy: Anisotropy | None  # by hand; not with trend_id
    pou: Pou
    data: DataSel | None  # None — groups without codes only (shells, expand, manual data)
    groups: tuple[Group, ...]  # by age: the first is the youngest (решение 15)
    manual: tuple[Manual, ...]
    post: Post

    def resolved(self, extent: tuple[float, ...]) -> ModelSpec:
        """`extent` — the box of the boundary (xmin, ymin, zmin, xmax, ymax, zmax): h by default; 422 `bad_param`
        {param: "h"} when h makes more than CELLS_AXIS_MAX cells along an axis."""
        size = [extent[i + 3] - extent[i] for i in range(3)]
        h = self.h if self.h is not None else resolve_h(math.sqrt(sum(d * d for d in size)))
        if max(size) / h > CELLS_AXIS_MAX:
            raise bad("bad_param", "Шаг h слишком мал для границы модели.", "Увеличьте шаг h или уменьшите охват.", "h")
        return replace(self, h=h)

    def group(self, gid: int) -> Group:
        for g in self.groups:
            if g.id == gid:
                return g
        raise refuse("group_not_found", group=gid)

    def s_of(self, g: Group) -> float:
        """s of 8.9.4: the given one, else 0 for contacts and 0,05 for grades (shells)."""
        if self.smoothing.s is not None:
            return self.smoothing.s
        return S_GRADE if g.type == "shell" else S_CONTACT

    def with_group(self, g: Group) -> ModelSpec:
        """The model with `g` replaced (an id it has) or added last — the oldest (no id or a new one: the next
        free number); checked as a whole."""
        if g.id is not None and any(x.id == g.id for x in self.groups):
            groups = tuple(g if x.id == g.id else x for x in self.groups)
        else:
            groups = (*self.groups, g if g.id is not None else replace(g, id=_next_id(self.groups)))
        return _checked(replace(self, groups=groups), "groups")

    def canonical(self) -> dict[str, Any]:
        return {"name": self.name, "boundary": self.boundary.canonical(), "h": self.h, "levels": self.levels,
                "kernel": self.kernel, "kernel_radius": self.kernel_radius, "smoothing": self.smoothing.canonical(),
                "trend_id": self.trend_id,
                "anisotropy": None if self.anisotropy is None else self.anisotropy.canonical(),
                "pou": self.pou.canonical(), "data": None if self.data is None else self.data.canonical(),
                "groups": [g.canonical() for g in self.groups], "manual": [m.canonical() for m in self.manual],
                "post": self.post.canonical()}  # fmt: skip

    def hash_params(self) -> dict[str, Any]:
        """What «устарела» and the fingerprint compare besides the inputs: the parameters without the name."""
        return {"algo": ALGO, **{k: v for k, v in self.canonical().items() if k != "name"}}


def _next_id(groups: tuple[Group, ...]) -> int:
    return max((g.id for g in groups if g.id is not None), default=0) + 1


def _numbered(groups: tuple[Group, ...]) -> tuple[Group, ...]:
    """Groups without an id get the next free numbers in their order (the ids given stay)."""
    out: list[Group] = []
    for g in groups:
        out.append(g if g.id is not None else replace(g, id=_next_id((*groups, *out))))
    return tuple(out)


def _checked(spec: ModelSpec, param: str) -> ModelSpec:
    """The groups within the model: count, unique ids and names, faults → groups of type `fault`, a code in one
    group only, codes need `data`, a group needs codes or manual data (domain, vein, fault)."""
    if len(spec.groups) > GROUPS_MAX:
        raise bad("bad_param", f"Групп больше {GROUPS_MAX}.", f"Оставьте не больше {GROUPS_MAX}.", param)
    ids = [g.id for g in spec.groups]
    names = [g.name.casefold() for g in spec.groups]
    faults = {g.id for g in spec.groups if g.type == "fault"}
    seen: dict[str, int] = {}
    manual = {m.group for m in spec.manual}
    for i, g in enumerate(spec.groups):
        p = f"{param}.{i}"
        if g.id is not None and g.id in ids[:i]:
            raise bad("bad_param", "Номер группы повторяется.", "Обновите вкладку модели.", f"{p}.id")
        if g.name.casefold() in names[:i]:
            raise bad("bad_param", f"Группа «{g.name}» уже есть.", "Задайте другое имя.", f"{p}.name")
        for k, f in enumerate(g.faults):
            if f not in faults:
                raise bad("bad_param", "Группу рассекает не разлом.", "Выберите группу вида «Разлом».",
                          f"{p}.faults.{k}")  # fmt: skip
        for k, c in enumerate(g.codes):
            if c in seen and seen[c] != i:
                raise bad("bad_param", f"Код «{c}» уже входит в другую группу.", "У кода одна группа.",
                          f"{p}.codes.{k}")  # fmt: skip
            seen[c] = i
        if g.codes and spec.data is None:
            raise bad("bad_param", "Не выбрана таблица кодов модели.", "Выберите набор, таблицу и поле кода.", "data")
        if g.type in ("domain", "vein", "fault") and not g.codes and g.id not in manual:
            raise bad("bad_param", "У группы нет данных: ни кодов, ни ручных слоёв.", "Выберите коды группы.",
                      f"{p}.codes")  # fmt: skip
    known = {i for i in ids if i is not None}
    for i, m in enumerate(spec.manual):
        if m.group not in known:
            raise bad("bad_param", "Ручной слой ссылается на несуществующую группу.", "Выберите группу слоя.",
                      f"manual.{i}.group")  # fmt: skip
    return spec


def parse_model(body: Mapping[str, Any]) -> ModelSpec:
    """The body of «Новая модель» / `PUT` (or its stored `meta`) → the checked parameters."""
    b = obj(body, "Параметры неявной модели", "body")
    for key in META_ONLY:
        b.pop(key, None)
    trend_id = id_of(b.get("trend_id"), "тренд", "trend_id", required=False)
    anis = parse_anisotropy(b.get("anisotropy"))
    if trend_id is not None and anis is not None:
        raise bad("bad_param", "Задайте тренд или анизотропию вручную — что-то одно.", "Уберите одно из двух.",
                  "anisotropy")  # fmt: skip
    kernel = choice(b.get("kernel"), KERNELS, "linear", "Ядро", "kernel")
    radius = ranged(b.get("kernel_radius"), "kernel_radius", "kernel_radius")
    groups = tuple(parse_group(g, f"groups.{i}")
                   for i, g in enumerate(list_of(b.get("groups") or [], "Группы", "groups", GROUPS_MAX)))  # fmt: skip
    spec = ModelSpec(
        name=text_of(b.get("name"), "Имя", "name", required=False), boundary=parse_boundary(b.get("boundary")),
        h=ranged(b.get("h"), "h", "h"), levels=_int(b.get("levels"), "levels", LEVELS, "levels"), kernel=kernel,
        kernel_radius=radius if kernel == "wendland" else None,
        smoothing=parse_smoothing(b.get("smoothing")), trend_id=trend_id, anisotropy=anis, pou=parse_pou(b.get("pou")),
        data=parse_data(b.get("data")), groups=_numbered(groups), manual=parse_manual(b.get("manual")),
        post=parse_post(b.get("post")),
    )  # fmt: skip
    return _checked(spec, "groups")


# --- the requests of a model ------------------------------------------------------------------------------------


def _version(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or raw != int(raw) or raw < 1:
        raise bad("bad_param", "Не задана версия модели.", "Перечитайте модель и повторите.", "expected_version")
    return int(raw)


@dataclass(frozen=True)
class BuildSpec:
    """`POST …/build` и `…/update`: `threads` None — the job decides (the cores of the slot)."""

    expected_version: int
    threads: int | None


def parse_build(body: Mapping[str, Any]) -> BuildSpec:
    b = obj(body, "Построение", "body")
    t = ranged(b.get("threads"), "threads", "threads")
    return BuildSpec(_version(b.get("expected_version")), None if t is None else int(t))


@dataclass(frozen=True)
class PreviewSpec:
    group_id: int | None  # a stored group, or
    group: Group | None  # a group of the form not yet saved
    limit: int


def parse_preview(body: Mapping[str, Any]) -> PreviewSpec:
    b = obj(body, "Предпросмотр точек", "body")
    gid = id_of(b.get("group_id"), "группа", "group_id", required=False)
    g = None if b.get("group") is None else parse_group(b["group"], "group")
    if (gid is None) == (g is None):
        raise bad("bad_param", "Задайте группу: номер или параметры — что-то одно.", "Выберите группу.", "group_id")
    return PreviewSpec(gid, g, _int(b.get("limit"), "limit", PREVIEW_DEFAULT, "limit"))


@dataclass(frozen=True)
class CheckSpec:
    """Д-32 `GET …/check?group&page&size&sort&format`."""

    group: int | None  # None — every group
    page: int
    size: int
    sort: str
    format: str


def parse_check(q: Mapping[str, Any]) -> CheckSpec:
    return CheckSpec(id_of(q.get("group"), "группа", "group", required=False),
                     _int(q.get("page"), "page", 1, "page"), _int(q.get("size"), "size", PAGE_DEFAULT, "size"),
                     choice(q.get("sort"), CHECK_SORTS, "residual", "Сортировка", "sort"),
                     choice(q.get("format"), FORMATS, "json", "Формат", "format"))  # fmt: skip


def parse_support(body: Mapping[str, Any], d_max: float = D_MAX) -> tuple[int | None, float]:
    """Д-32 `POST …/support {wireframe_id?, threshold?}` → (output wireframe | None — every surface, D_оп)."""
    b = obj(body, "Карта опоры", "body")
    wid = id_of(b.get("wireframe_id"), "поверхность модели", "wireframe_id", required=False)
    return wid, _num(b.get("threshold"), "support", SUPPORT_FACTOR * d_max, "threshold")


@dataclass(frozen=True)
class CompareSpec:
    """Д-33 `POST …/compare_holes {set_id, table, field, page?, size?}`."""

    set_id: int
    table: str
    field: str
    page: int
    size: int


def parse_compare(body: Mapping[str, Any]) -> CompareSpec:
    b = obj(body, "Сверка со скважинами", "body")
    set_id = id_of(b.get("set_id"), "набор скважин", "set_id")
    table = text_of(b.get("table"), "Таблица кодов", "table")
    field = text_of(b.get("field"), "Поле кода", "field")
    assert set_id is not None and table is not None and field is not None
    return CompareSpec(set_id, table, field, _int(b.get("page"), "page", 1, "page"),
                       _int(b.get("size"), "size", PAGE_DEFAULT, "size"))  # fmt: skip


def parse_relations(body: Mapping[str, Any], spec: ModelSpec) -> ModelSpec:
    """Э19б `PUT …/relations {expected_version, order: [gid …], relations: {gid: relation}}` → the model with the
    groups reordered (faults keep their place after the others) and their relations set."""
    b = obj(body, "Стек отношений", "body")
    stack = [g for g in spec.groups if g.type != "fault"]
    order = _ids(b.get("order"), "Порядок групп", "order", GROUPS_MAX) if b.get("order") is not None else tuple(
        g.id for g in stack if g.id is not None)
    if sorted(order) != sorted(g.id for g in stack if g.id is not None):
        raise bad("bad_param", "Порядок должен содержать каждую группу стека один раз.", "Перетащите группы заново.",
                  "order")  # fmt: skip
    rel = obj(b.get("relations"), "Отношения групп", "relations")
    by_id = {g.id: g for g in stack}
    for key in rel:
        if not str(key).isdigit() or int(key) not in by_id:
            raise bad("bad_param", "Отношение задано несуществующей группе.", "Обновите вкладку модели.",
                      f"relations.{key}")  # fmt: skip
    groups = [replace(by_id[i], relation=choice(rel.get(str(i)), RELATIONS, by_id[i].relation, "Отношение группы",
                                                f"relations.{i}")) for i in order]  # fmt: skip
    for g, old in zip(groups, (by_id[i] for i in order), strict=True):
        if old.type != "domain" and g.relation != old.relation:  # only a domain is in the stack: no silent no-op
            raise bad("bad_param", f"У группы «{g.name}» нет отношения к другим группам.",
                      "Отношения задаются доменам литологии: оболочки, жилы и расширения в стек не входят.",
                      f"relations.{g.id}")  # fmt: skip
    return _checked(replace(spec, groups=(*groups, *(g for g in spec.groups if g.type == "fault"))), "groups")


# --- the forms of the window ---------------------------------------------------------------------------------


def defaults_json() -> dict[str, Any]:
    """The defaults of 8.9.4 the wizard fills its form with (`GET /implicit/defaults` → `params`)."""
    return {"h": None, "h_divisor": H_DIVISOR, "levels": LEVELS, "kernel": "linear", "kernel_radius": None,
            "smoothing": {"mode": "manual", "s": None, "cond_target": COND_TARGET,
                          "s_contact": S_CONTACT, "s_grade": S_GRADE},
            "pou": {"n_patch": N_PATCH, "n_min": N_MIN, "delta": DELTA},
            "data": {"d_max": D_MAX, "step": STEP}, "trend_radius": TREND_RADIUS,
            "shell": {"source": "composites", "transform": "linear", "mode": "numeric", "p": P_INDICATOR},
            "vein": {"bound": "hull", "offset": 0.0, "t_min": 0.0},
            "post": {"v_min": 0.0, "simplify": False, "clip": "boundary"}, "support_factor": SUPPORT_FACTOR,
            "choices": {"kernels": list(KERNELS), "group_types": list(GROUP_TYPES), "relations": list(RELATIONS),
                        "roles": list(ROLES), "transforms": list(TRANSFORMS), "shell_modes": list(SHELL_MODES),
                        "shell_sources": list(SHELL_SOURCES), "vein_bounds": list(VEIN_BOUNDS),
                        "clips": list(CLIPS), "point_types": list(POINT_TYPES)},
            "limits": {"groups": GROUPS_MAX, "codes": CODES_MAX, "thresholds": THRESHOLDS_MAX, "manual": MANUAL_MAX,
                       "veins": VEINS_MAX, "points": POINTS_MAX, "preview": PREVIEW_MAX}}  # fmt: skip


def ranges_json() -> dict[str, Any]:
    """The ranges for the form: key → {lo, hi, lo_open, integer, unit} (null — unbounded)."""
    return {k: {"lo": None if math.isinf(r.lo) else r.lo, "hi": None if math.isinf(r.hi) else r.hi,
                "lo_open": r.lo_open, "integer": r.integer, "unit": r.unit.strip()}
            for k, r in RANGES.items()}  # fmt: skip
