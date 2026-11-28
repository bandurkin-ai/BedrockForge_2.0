# Э17 — контракт ядра блочных моделей и фильтров (для дорожек Х, Э, Ф, К1, К2, И-А, И-Б, И-В; удаляется в 17.42)

Источник истины — ADR 0023 §2–15, ТЗ 7.31, 8.7, 7.35 Д-02, Д-13. Схема — `core/bf_core/blocks/schema.py` (сетка
`GridV2`, субблоки `SubblockSpec`, поворот, порядок строк) и `core/bf_core/blocks/meta.py` (описание v2, `upgrade`,
`public_meta`) — 17.1, готово. Фикстуры настоящих ответов — `core/tests/data/blocks/{grids.json,
contract_answers.json}` (`BF_REGEN_BLOCKS_CONTRACT=1 uv run pytest -q tests/test_blocks_schema_v2.py`). Изменение
формы ниже — только через оркестратора. Префикс маршрутов — `/api/v1`.

## 1. Общие правила

- `X-BF-Token`, Host/Origin — как у всего ядра. Координаты, объёмы, значения — float64; пусто — `null` в JSON
  (NaN в массивах BFA1), никогда не 0. Числа на входе — число или текст «0,5» / «0.5»; целые — целое (15 или
  15.0). Нет ключа / `null` / `""` — умолчание.
- Ошибка — `{code, message, hint, param?, …}` по-русски; `param` — путь в теле: `"grid.div.0"`, `"rules.items.1.cond.value"`,
  `"wireframes.0.id"`. Нет открытого проекта — 409 `no_project`. Запись в проект «только просмотр» — 423
  `project_read_only` (всё, что пишет модель, поля, фильтр, схемы категорий; показ, отчёт, экспорт, «Проверить на
  модели» работают).
- Оптимистичная блокировка: всё, что меняет существующую модель или фильтр, несёт `expected_version` (версия
  прочитанного объекта). Нет ключа или другая версия — 409 `version_conflict {version}` (текущая), до записи и
  ещё раз при фиксации задания (модель изменили, пока задание считало, — задание падает с этим кодом, записанное
  другим не затирается). Окна на 409 предлагают «Перечитать».
- Задание: ответ маршрута `{source, id}`; ход и итог — `GET /jobs/{source}/{id}` (`status`, `progress`,
  `message` — название стадии, `result`, `error {code, message, hint, param?}`). Синхронные проверки тела (404,
  422, 409) — до постановки задания. Итог несёт `events: [[имя, данные]]` — их публикует планировщик.
- Синхронные расчёты показа — в слоте HEAVY (`stats.routes_charts.SLOTS`). Обгон: тело несёт `channel` (строка
  1–64, например `"scene:12"`) и `seq` (целое ≥ 0); новый запрос канала отменяет выполняемый с меньшим `seq`
  (ядро проверяет отмену между порциями), отменённый отвечает 409 `superseded` — интерфейс его не показывает.
- Событие `objects.changed {kind: "block_model" | "filter", id, deleted?}` — после каждой записи и удаления.
- Пока маршрут не подключён в сборке — 422 `not_ready`.

## 2. Модель v2 (`objects(kind="block_model").meta`)

Форма — докстрока `blocks/schema.py`; проверка и перевод v1 → v2 — `blocks/meta.py::upgrade` (идемпотентно).
v1 читается как v2: `rotation` 0, `div` [1, 1, 1], `rows` = `parents` = v1 `cells`, `sub`/`full` false, поля —
`dtype` f8 (число) или `cat` (категория), `file: "cells"`, `role`/`description`/`source` null; `source` модели —
`{op: "import", format, file, element}` (null остаётся null); `checks`, `groups`, `inputs`, `algo` — null. Файлы
v1 не переписываются; первая запись v2 (поле калькулятора и т. п.) сохраняет `meta` уже как v2.

| Что | Правило |
|---|---|
| Точка | p = O + R_z(φ)·q; O = `grid.corner` (минимум локальных координат), φ = `grid.rotation` ∈ (−180°; 180°], против часовой при виде сверху; sin/cos кратных 90° — точные (`GridV2.sin_cos`) |
| Сетка | `counts` [nx, ny, nz], nx·ny·nz ≤ 2·10⁹ (`MAX_GRID_V2`; сетка v1 — ≤ 2·10⁸, `MAX_CELLS`), `size` [dx, dy, dz] или `tensors` (переменный шаг OMF: показ, фильтр, отчёт, экспорт; субблоки, заполнение, регуляризация — 422 `variable_grid {param: "grid.tensors"}`) |
| P | P = i + nx·(j + ny·k), uint64 (ADR 0017; Мортон ТЗ 6.5/8.7.2 — вопрос № 62) |
| Субблоки | `div` [nx, ny, nz] — любое целое 1…128 по оси (ADR 0030: 5, 3, 2·3 …; не только 2^L — «уровни» L есть, только если все `div` степени двойки); строка — (P, s, w): s = sx sy sz (смещение в подъячейках, 0…div−1), w = wx wy wz (размер, ≥ 1, s + w ≤ div), uint8; родитель целиком — s = 0, w = div. Центр строки в локальных осях: q_a = (i_a + (s_a + w_a/2)/div_a)·d_a |
| Порядок строк | (P, sz, sy, sx) строго по возрастанию — все файлы модели (`schema.sort_rows`, `schema.ascending`) |
| Объём строки | Π_a(w_a·d_a/div_a) · FRAC, если у модели есть поле `role: "frac"` (не больше одного); FRAC пусто — блок не входит в объём (пропуск не ноль: объём `null`) |
| `rows`, `parents`, `sub`, `full` | строк файлов; занятых родителей; есть столбцы sx…wz; прототип (0 строк), у которого все родители считаются занятыми (№ 66, по умолчанию false) |
| Поле | `{name, kind: number\|category, dtype: f8\|f4\|i4\|i2\|i1\|cat, unit, description (≤ 500), role: grade\|density\|frac (число) \| code\|class (категория) \| null, labels? (cat), stats, file: "cells"\|"fld:<8 hex>", source}`; имена — 1–100 символов, без учёта регистра не повторяются, не `idx`, а при `sub` — не `sx`…`wz` |
| `source` поля | `{op: import\|calc\|fill\|code\|density\|classify\|regularize\|estimate, params {…}, inputs [sha256 файлов, из которых считано], algo, versions {numpy, numexpr, numba}, at}` или null (поле импорта v1) |
| `stats` | число: `{count, missing, min, max, mean, std}` (std — делитель n; без значений — null); категория: `{count, missing, counts [по labels]}` |
| `checks` | `{at, duplicates, overlaps {n, method: exact\|volume}, empty [имена], constant [имена], volume, examples [≤ 20 {check: outside\|crossing\|duplicate\|overlap, row, p, sub [6] \| null, other_row?}]}` или null (ещё не проверено) |
| `groups` | `[{rows, k0, k1}]` — группы строк по 2²⁰ (последняя короче) и охват слоёв k; null — v1 (группы — по метаданным Parquet) |
| `source` модели | `{op: import\|prototype\|fill\|code\|regularize, format?, file?, element?, params?}` или null |
| `inputs`, `algo` | `{wireframes \| model: [{id, sha}]}` — входы fill / code (дробление) / regularize; «устарел» = sha ≠ текущему |

Файлы (blobs владельца `object`):
- `cells` — Parquet zstd: `idx` uint64 (P), при `sub` — `sx sy sz wx wy wz` uint8, затем поля импорта. Группы строк —
  2²⁰ (v1 — 10⁶). Типы: f8 → double, f4 → float (NaN — пусто), i4/i2/i1 → int32/int16/int8 и cat → int16 (null
  Parquet — пусто; ≤ 32 767 подписей, больше — 422 `too_many_labels`); категория v1 — int32, −1 — пусто
  (читатель различает по физическому типу столбца).
- `fld:<key>` (8 hex, `schema.new_field_file()`) — поля одного расчёта, те же строки и те же группы строк, что у
  `cells`, без `idx`; неизменяем. Замена поля — новый файл; прежний уходит при сборке мусора, когда в нём не
  осталось полей модели. Строка оркестратора: префикс `fld:` в `db/schema.py::BLOB_ROLE_PREFIXES`.
- `cache\blocks\<sha cells>\` — распакованные `.npy` (memmap), доли каркасов, оболочки; удаляются без потерь.

В памяти (все ядра, `Model.columns`): числовые поля любого `dtype` — float64, NaN — пусто; категории — int32 коды в
`labels`, −1 — пусто; P — uint64; s, w — uint8 n×3.

Потребители Э11/Э13 на модели с `rotation ≠ 0` или `sub` отвечают 422 `blocks_unsupported` «Модель с поворотом
или субблоками здесь пока не поддерживается.», пока их не научат: swath — отказ ставит 17.4 в
`stats/blocks_cache.py`, снимает 17.10; экспорт OMF — 17.15 (`exporting/omf_job.py`: поворот Z пишется, субблоки —
`omf_subblocks`).

## 3. Интерфейсы Python

```python
# blocks/schema.py (17.1, готово)
@dataclass(frozen=True)
class SubblockSpec:
    div: tuple[int, int, int] = (1, 1, 1)
    @classmethod
    def of(cls, raw: Sequence | None) -> SubblockSpec          # 422 subblock_div {param: "grid.div.N"}
    @classmethod
    def of_levels(cls, levels: Sequence[int]) -> SubblockSpec  # L 0…7 → 2^L (прототип и заполнение — по-прежнему 2^L)
    levels: tuple[int, int, int] | None; sub: bool             # None — div не 2^L (ADR 0030); свойства
    def bad_rows(self, s, w) -> np.ndarray[bool]               # n×3 → строки не по подъячейкам

@dataclass(frozen=True)
class GridV2:
    corner: tuple[float, float, float]; counts: tuple[int, int, int]
    size: tuple[float, float, float] | None; tensors: tuple[tuple[float, ...], ...] | None
    rotation: float; sub: SubblockSpec
    @classmethod
    def of(cls, raw: Mapping) -> GridV2      # meta.grid v1 или v2; 422 block_model_invalid / _too_large /
                                             # rotation_unsupported / subblock_div / variable_grid
    def to_meta(self) -> dict                # {corner, counts, size|tensors, order "F", rotation, div}
    cells: int                               # nx·ny·nz
    def regular(self, what="Операция") -> tuple[float, float, float]   # size или 422 variable_grid
    def subcell(self) -> tuple[float, float, float]                    # size / div
    def steps(self, axis) -> list[float]; def edges(self, axis) -> list[float]   # локальные, от 0
    def lengths(self) -> tuple[float, float, float]
    def sin_cos(self) -> tuple[float, float]; def matrix(self) -> list[list[float]]   # R_z(φ) по строкам
    def to_project(self, q) -> np.ndarray    # n×3 локальные → проекта (поэлементно, без BLAS)
    def to_local(self, p) -> np.ndarray      # q = R_z(−φ)·(p − O)
    def extent(self) -> list[float]          # охват повёрнутой сетки в осях проекта → objects.extent

normalize_angle(deg) -> float; parse_rotation(raw: number | {x?, y?, z?} | None) -> float
rotation_of_angles([(axis "x"|"y"|"z"|1|2|3, deg)]) -> float   # Datamine ANGLEn/ROTAXISn (17.14)
rotation_of_axes(u, v, w, tol=1e-9) -> float                     # OMF axis_u/v/w (17.15)
sort_rows(p, s=None) -> np.ndarray; ascending(p, s=None) -> bool; new_field_file() -> "fld:xxxxxxxx"
# ошибки: rotation_unsupported(param), bad_div(axis, value), variable_grid(what) → ApiError 422 с param

# blocks/meta.py (17.1, готово)
upgrade(meta: Mapping) -> dict        # v1|v2 → проверенная v2; 422 block_model_invalid
check_field_v2(raw, *, sub=False) -> dict; grid_of(meta) -> GridV2
files_of(meta) -> dict[str, list[str]]            # {"cells": [...], "fld:…": [...]}
public_meta(meta, *, brief=False) -> dict         # часть ответа GET /blocks[/{id}] (§4)

# blocks/geometry.py (17.2): строки → геометрия, float64; rows — None (все) или индексы строк np.intp
row_ijk(grid, p) -> (i, j, k) intp; row_local_centres(grid, p, s, w) -> n×3; row_boxes(grid, p, s, w) -> n×6
(lo3, hi3 локальные); row_volumes(grid, p, s, w, frac=None) -> n; centres(grid, p, s, w) -> n×3 проекта
# s, w = None — модель без субблоков (s = 0, w = div)

# blocks/model.py (17.4) — чтение
class Model:
    id: int; name: str; version: int; meta: dict (v2); grid: GridV2; rows: int; parents: int; sub: bool
    full: bool; fields: list[dict]
    @classmethod
    def open(cls, conn, store: BlobStore, model_id: int) -> Model   # 404 block_model_not_found, 409 _broken
    def field(self, name: str) -> dict                               # 404 block_field_not_found
    def p(self) -> np.ndarray                                        # uint64 [rows]
    def subs(self) -> tuple[np.ndarray, np.ndarray] | None           # (s, w) uint8 n×3
    def columns(self, names: Sequence[str]) -> dict[str, np.ndarray] # §2 «в памяти»
    def iter(self, names: Sequence[str] = (), chunk: int = 1 << 20) -> Iterator[Chunk]
    # Chunk(start: int, p: uint64[n], s, w: uint8[n, 3] | None, cols: dict[str, np.ndarray])
    def centres(self, rows=None) -> np.ndarray; def local(self, rows=None) -> np.ndarray
    def boxes(self, rows=None) -> np.ndarray; def volume(self, rows=None) -> np.ndarray
    def shas(self) -> dict[str, str]                                 # роль файла → sha256

# blocks/fields_store.py (17.3) — единственный путь записи полей
class FieldsWriter:                                  # контекстный менеджер: исключение — временный файл удалён
    def __init__(self, store: BlobStore, model: Model, expected_version: int) -> None
    def add(self, name, dtype="f8", *, unit=None, role=None, description=None, labels=None, replace=False) -> None
    def write(self, values: Mapping[str, np.ndarray]) -> None   # следующая порция строк по порядку, §2 «в памяти»
    def finish(self) -> StagedFields
def commit_fields(conn, store, staged: StagedFields, *, source: Mapping) -> int   # новая version
# 409 version_conflict {version}; поле есть и replace=False — 409 field_exists {field}; строк не rows — 422

# calc (17.5–17.6)
compile(text: str, schema: Mapping[str, {"kind", "labels"?}], *, dialect="blocks") -> Program
# 422 expr_syntax / expr_field / expr_type {pos}
class Program: text: str; fields_used: list[str]; out_kind: "number" | "bool" | "category"; labels: list[str]
    def eval(self, cols: Mapping[str, np.ndarray]) -> np.ndarray   # number f64, bool bool_, category int32 (−1)

# blocks/restrict.py (17.8)
evaluate(model: Model, node: Mapping | None, *, conn, store, cancel=None) -> np.ndarray   # f64 [rows] ∈ [0; 1]
# node None — все 1.0; 422 filter_field_missing {field, model}, 404 wireframe_not_found

# ядра К1/К2 (17.17–17.26): вход — GridV2 и массивы строк (p, s, w), треугольники — grid.to_local(v) float64;
# выход — массивы в порядке строк или по P по возрастанию; побитно при 1, 2, 4, 8 потоках
# 17.22 blocks/shell.py:
exposed(grid, p, s, w, passed: bool[n], klass: uint16[n]) -> Faces
# Faces: c f64 [m, 3] (центр грани, локальные), h f64 [m, 2] (полуразмеры по осям u, v), a uint8 [m]
# (ось·2 + сторона), k uint16 [m], row int64 [m]; порядок — (a, плоскость, k, v, u)
```

Грань: ось a ∈ {0 X, 1 Y, 2 Z}, сторона 0 — нормаль −a, 1 — +a; оси плоскости u = (a + 1) mod 3, v = (a + 2) mod 3
(X → (Y, Z), Y → (Z, X), Z → (X, Y)).

## 4. Модели: `GET /blocks`, `GET /blocks/{id}`, `PATCH`, `DELETE`

Ответ — строка объекта + `meta.public_meta`: `{id, name, folder, version, extent, crs, size, created_at,
updated_at, stale, stale_reason, …public_meta}`. `size` — байты всех файлов модели (`cells` + `fld:`). Настоящий
ответ v1 сейчас и ожидаемый v2 (`get_v2`, `get_v2_sub`) — `contract_answers.json`.

```json
{"id": 12, "name": "Модель", "folder": null, "version": 3, "extent": [950.5, 2000.25, -300.0, 1130.4, 2161.9, -250.0],
 "crs": null, "size": 81920, "stale": false, "stale_reason": null, "meta_v": 2,
 "grid": {"corner": [1000.5, 2000.25, -300.0], "counts": [30, 20, 20], "size": [5.0, 5.0, 2.5], "order": "F",
          "rotation": 30.0, "div": [8, 8, 4], "variable": false, "levels": [3, 3, 2]},   # levels: null при div [5, 5, 3]
 "cells": 5210, "rows": 5210, "parents": 1200, "sub": true, "full": false,
 "fields": [{"name": "AU", "kind": "number", "dtype": "f8", "unit": "г/т", "description": null, "role": "grade",
             "stats": {"count": 5000, "missing": 210, "min": 0.01, "max": 12.3, "mean": 1.1, "std": 0.9},
             "file": "cells", "source": null}],
 "checks": null, "groups": [{"rows": 5210, "k0": 0, "k1": 19}], "source": {"op": "import", "format": "dat",
 "file": "model.dat", "element": null}, "warnings": [], "inputs": null, "algo": null,
 "created_at": "…", "updated_at": "…"}
```

- `GET /blocks` → `{block_models: [ответ с brief]}`: сетка без `tensors`, поля `{name, kind, dtype, unit, role}`,
  без `checks`, `groups`, `inputs`, `algo`. `cells` = `rows` (имя Э11 остаётся).
- `stale` — `inputs` модели не совпадают с текущими (каркас удалён или изменён, модель-источник изменена);
  `stale_reason` — «изменён каркас «…»», «удалён каркас «…»», «изменена модель «…»». У поля — `stale` тем же
  правилом по `source.inputs` (sha файлов, которых у модели больше нет).
- `PATCH /blocks/{id}` `{expected_version, name?, fields?: [{name, unit?, description?}]}` → ответ `GET`; хотя бы
  одно из `name`, `fields`, иначе 422 `bad_param {param: "name"}`; поля без такого имени — 404
  `block_field_not_found {param: "fields.N.name"}`; `unit` ≤ 40, `description` ≤ 500 символов; 409
  `version_conflict {current}` (форма Э11), `name_taken`; 423. Пишет `meta` v2.
- `DELETE /blocks/{id}` → `{deleted}`; используют другие объекты (`object_refs`) — 422 `block_model_in_use
  {objects}`; 423.

## 5. Импорт — задание `import.objects` (`POST /import/objects/inspect`, `POST /import/objects`)

Параметры блочного файла — `options[<путь>].blocks` (строка оркестратора: поле `blocks: dict | None` в
`importing/router.py::ObjectOptions`):

```json
{"roles": {"x": "XC", "y": "YC", "z": "ZC", "dx": "XINC", "dy": "YINC", "dz": "ZINC"},
 "grid": {"corner": [x, y, z], "size": [dx, dy, dz], "counts": [nx, ny, nz], "rotation": 0, "div": [1, 1, 1]} ,
 "centres": "project", "empty": [-99], "fields": {"AU": {"unit": "г/т", "role": "grade", "skip": false}}}
```

`roles` — столбцы центра и размера (нет `dx…dz` — регулярная модель); `grid` — null: выводится (`grid_infer`:
min(центр) − размер/2, шаг по уникальным центрам, начало кратно размеру; DM — XMORIG… из файла); `centres` —
`project` | `model` (в каких осях записаны центры, для повёрнутой модели); `empty` — коды пустого (по умолчанию []
— мастер предлагает −99, если встречается); `fields` — единица, роль, пропуск поля.

`inspect` → `files[].blocks`:

```json
{"format": "dat" | "csv" | "csv_micromine" | "dm" | "fbm" | "omf", "rows": 169070,
 "columns": [{"name": "XC", "type": "number" | "text", "role": "x" | "y" | "z" | "dx" | "dy" | "dz" | null}],
 "roles": {"x": "XC", "…": "…"}, "grid": {…GridV2.to_meta()} | null, "sub": false, "parents": 169070,
 "empty_candidates": [-99], "units": {"AU": "г/т"}, "check3": {"ok": true, "dist": 0.0, "rows": [0, 84535, 169069]},
 "warnings": ["…"], "error": null | {"code", "message", "hint", "param?"}}
```

Итог задания — запись `files[]`: `{file, format, kind: "block_model", object_id, rows, parents, sub, checks}`.
Отказ файла (строка журнала и `failed[]`): 422 `blocks_unaligned {count, rows ≤ 20}` (блок за началом сетки,
субблок через границу родителя или не по подъячейкам), `rotation_mismatch {rows, dist}` (три центра — первая,
средняя, последняя строки — дальше 1e−6 м от вычисленных), `rotation_unsupported`, `subblock_div`,
`variable_grid`, `block_model_too_large`, `too_many_labels`, `disk_full`. Дубликаты (остаётся первая строка),
перекрытия, пустые и постоянные поля, объём — в `meta.checks`. Стадии: «Чтение», «Сортировка», «Запись».

## 6. Прототип

`POST /blocks/prototype/preview` (синхронно) и `POST /blocks/prototype` (задание `blocks.prototype`, `quick`):

```json
{"size": [10, 10, 5], "object_ids": [3, 7] | null, "extent": [xmin, ymin, zmin, xmax, ymax, zmax] | null,
 "pad": 20 | [20, 20, 10], "rotation": 30, "levels": [3, 3, 2], "full": false, "name": "Прототип", "folder": null}
```

Ровно одно из `object_ids` (охват объектов, `objects.extent`) и `extent` (иначе 422 `bad_param {param:
"object_ids"}`); `pad` ≥ 0 (0); `rotation` — φ (0); `levels` 0–7 (0); `full` — № 66 (false). Начало кратно
размеру в повёрнутых осях от начала координат проекта: q′ = R_z(−φ)·p по 8 углам охвата, O′ = ⌊(min q′ − pad)/d⌋·d,
n = ⌈(max q′ + pad − O′)/d⌉, O = R_z(φ)·O′. Предпросмотр:

```json
{"grid": {…GridV2.to_meta()}, "parents": 2400000, "disk": 43200000, "extent": [..6],
 "frame": [[x, y, z], "… 8 углов: q ∈ {0, L}³ в порядке x быстрее, затем y, затем z"]}
```

`disk` — оценка без сжатия: parents × (8 + 6·[L > 0] + 10) байт («≈» в окне). Ошибки: 404 `object_not_found
{param: "object_ids.N"}`, 422 `no_extent` (у объектов нет охвата), `bad_param`, `block_model_too_large`. Итог
задания: `{model_id, name, grid, parents, events}`; модель — 0 строк, `source {op: "prototype", params}`.

## 7. Заполнение, кодирование, «Перестроить» (`heavy`)

`POST /blocks/fill` → задание `blocks.fill`:

```json
{"prototype_id": 21, "wireframes": [{"id": 5, "code": "Руда", "priority": 2}, {"id": 6}],
 "mode": "inside" | "outside" | "both", "levels": [3, 3, 2] | null, "m": 4, "outside_code": "вне",
 "code_field": "CODE", "frac_field": "FRAC", "name": null, "folder": null}
```

`code` — по умолчанию имя каркаса; `priority` — целое, больше — главнее (по умолчанию: первый в списке — главный,
равные — раньше в списке); `levels` — null: `div` прототипа; `m` 1–16 (4). Каркас проходит `check_stats`,
иначе 422 `wireframe_open {param: "wireframes.N.id", name}` / `wireframe_orientation`; переменный шаг — 422
`variable_grid`. Новая модель «‹прототип› (заполнение)»: поля CODE (`cat`, `role: code`, подписи — коды
(+ `outside_code`)) и FRAC (f8, `role: frac`); `source {op: fill, params}`, `inputs {wireframes: [{id, sha}]}`.
Стадии: «Каркасы», «Столбцы», «Граничные родители», «Дробление», «Слияние», «Запись». Итог: `{model_id, name,
rows, parents, codes: {код: строк}, volume: {код: м³}, stages: {секунды}, events}`.

`POST /blocks/{id}/code` → задание `blocks.code`: `{expected_version, wireframes, mode, split: false, m: 4,
outside_code, code_field, frac_field, name}`. `split: false` — поле `code_field` той же модели по центру строки
(FieldsWriter; субблоки не меняются); `split: true` — новая модель «‹имя› (кодирование)» с дроблением граничных,
потомки наследуют поля родителя, CODE и FRAC как у fill. Итог: `{model_id, fields | name, …как fill}`.

`POST /blocks/{id}/rebuild` `{expected_version}` → задание того же вида (`fill`, `code` с дроблением,
`regularize`) с `source.params` на текущих входах; модель переписывается на месте (тот же id, version + 1); поля
других операций (`source.op` ≠ операции модели) удаляются — их список в итоге (`dropped`), окно спрашивает до
запуска. Поле: `POST /blocks/{id}/fields/{name}/rebuild` `{expected_version}` — повтор `calc` / `density` /
`classify` по `source.params`. Источник удалён — 409 `source_lost`; у модели нет `source.op` для перестройки —
422 `not_rebuildable`.

## 8. Калькулятор (8.7.4)

`POST /blocks/calc/check` (синхронно) `{model_id, expr, dtype?}` → `{text, fields_used, out_kind: number | bool |
category, dtype}` (`dtype` — f8 у number, i1 у bool, cat у category; заданный — проверяется). Ошибки 422 с `pos`
(номер символа от 0, кодовые точки): `expr_syntax {pos, message}` (≤ 4000 символов, глубина ≤ 64,
`__import__('os')` — синтаксическая ошибка без выполнения), `expr_field {pos, field}` (нет поля в модели),
`expr_type {pos}` (например, текст + число; cat из числового выражения).

`POST /blocks/{id}/calc` → задание `blocks.calc` (`quick`):

```json
{"expected_version": 3, "expr": "IF(AU > 1, AU*2, 0)", "field": "AU2", "dtype": "f8", "unit": "г/т",
 "description": null, "filter_id": null, "restrict": null, "outside": "keep" | "nan", "replace": false}
```

Поле есть и `replace: false` — 409 `field_exists {field}` (окно спрашивает «Заменить?» — № 69); `outside` —
«оставить» (новое поле — пусто) или NaN вне ограничителя; i* из NaN → null; нецелое в i* — округление к ближайшему
(половина — от нуля). Итог: `{model_id, field, version, rows, passed, missing, stats, events}`;
`source {op: calc, params: {expr, text, dtype, filter_id, restrict, outside}, inputs}`. Правила NaN — ADR §7.

## 9. Плотность, категории, регуляризация

`POST /blocks/{id}/density` → `blocks.density` (`quick`): `{expected_version, method: constant | field | table |
formula, value?, field?, table?: {field, values: [{value, density}], default}, expr?, default, out: "DENS",
src_field: "DENS_SRC", filter_id?, restrict?, replace: false}`. Выход — `out` (f8, `role: density`) и `src_field`
(i1: 0 — своё значение, 1 — подставлено `default`; вне ограничителя — null). Итог: `{model_id, fields, version,
rows, defaulted, stats, events}`.

`GET /blocks/schemes` → `{schemes: [{id: "jorc" | "gkz" | "<свой>", name, version, rules: [{label, expr | null}]}]}`
(справочник проекта в `settings`, ключ `block_class_schemes`; ГКЗ — шаблон A, B, C1, C2 без порогов, № 68);
`PUT /blocks/schemes/{id}` `{expected_version?, name, rules}` → схема (новая — без версии); 409; 423.

`POST /blocks/{id}/classify` → `blocks.classify` (`quick`): `{expected_version, rules: [{label, expr}] | scheme:
"jorc", overrides: [{wireframe_id, label}], majority: 0 (0–10 итераций), estimate: "AU_OK", out: "CLASS",
filter_id?, restrict?, replace: false}`. Ровно одно из `rules`, `scheme`; правило без `expr` — 422
`scheme_incomplete {param: "rules.N.expr"}`. Выход — `out` (`cat`, `role: class`). Итог: `{model_id, field,
version, counts: {подпись: строк}, unclassified, events}`.

`POST /blocks/{id}/regularize` → `blocks.regularize` (`heavy`): `{size: [dx, dy, dz], grades: [{field, weight:
"volume" | "tonnes"}] | null, density: "SG" | 2.7 | null, categories: ["CODE"] | null, name, folder}`. `grades`
null — все числовые поля по объёму; `categories` null — все категории (мода по объёму + поля `P_<КОД>` f8);
`weight: tonnes` без `density` — 422 `bad_param {param: "density"}`. Начало — кратно новому шагу в осях модели от
O; φ прежний. Итог: `{model_id, name, rows, totals: {before, after: {volume, tonnes, metal: {поле: число}}},
events}`.

## 10. Отчёт «Содержание–тоннаж» — `POST /blocks/{id}/report` → `blocks.report` (`quick`)

```json
{"grades": ["AU_CUT"] | null, "density": "SG" | 2.7 | null, "cutoffs": [0, 0.5, 1], "cutoff_field": null,
 "filter_id": null, "restrict": null, "categories": {"field": "CLASS", "values": ["Измеренные"]} | null,
 "groups": "DOMAIN" | null, "curve": 50, "csv": false}
```

Умолчания (7.31): `grades` — поля `*_CUT`, иначе `role: grade`; `density` — меняющееся поле `SG`, затем `role:
density` (постоянное — пропускается); `cutoff_field` — первое из `grades`; `cutoffs` — [0]. Ответ (`job.result`):

```json
{"density": {"field": "SG", "value": null}, "cutoff_field": "AU_CUT",
 "rows": [{"group": null, "cutoff": 0.0, "blocks": 5000, "volume": 1.2e6, "tonnes": 3.2e6,
           "grades": {"AU_CUT": {"grade": 1.1, "unit": "г/т", "metal": 113.2, "metal_unit": "тыс. унц",
                                 "missing_tonnes": 0.0}}}],
 "curve": {"cutoffs": [..50], "tonnes": [..], "grade": [..]}, "excluded": {"filtered": 0, "no_density": 12},
 "csv": null | "текст CSV (разделитель «;», десятичная запятая)"}
```

Тоннаж = объём × FRAC × f × плотность; борт «≥»; содержание — взвешенное по тоннажу блоков, где оно есть;
металл: г/т и ppm → тыс. унц (31,1034768 г), % → т, ppb → кг; единица неизвестна — `metal` null.

## 11. Проверка и экспорт

`POST /blocks/{id}/check` → `blocks.check` (`quick`) `{}`: пересчитывает `meta.checks` (версия не меняется); итог
`{model_id, checks, events}`.

`POST /blocks/{id}/export` → `blocks.export` (`heavy`): `{format: "dat" | "csv" | "omf" | "parquet", path, fields:
null | [имена], filter_id?, restrict?, header: "plain" | "micromine", encoding: "cp1251" | "bocu1", overwrite:
false}`. Столбцы DAT/CSV: X, Y, Z (центр, оси проекта), DX, DY, DZ (размер по осям модели), затем поля; Parquet —
`idx`, `sx…wz` (при `sub`), X, Y, Z, поля, метаданные `bf2.block_model` = `{grid}`. Синхронно: 409 `files_exist
{files}`, 422 `bad_path {param: "path"}` (в папке проекта), `bad_param {param: "path"}` (не полный, нет папки),
`omf_subblocks` «только после регуляризации». Работает в «только просмотр». Итог: `{path, format, rows, bytes}`.

## 12. Показ (синхронно, слот HEAVY, обгон по `channel`/`seq`)

Общие ключи тела: `filter_id: int | null`, `restrict: Node | null` (быстрый ограничитель окна; с фильтром —
И, произведение долей), `colour`, `channel`, `seq`. Блок проходит при f > 0.

```json
"colour": {"field": "AU" | null, "kind": "none" | "scale" | "classes" | "categories",
           "from": 0.1, "to": 10, "log": false, "breaks": [0.5, 1, 2], "hidden": [2]}
```

Класс строки k (uint16): `none` — 0; `scale` — 256 корзин: t = (v − from)/(to − from) (`log` — по ln, from > 0),
k = clamp(⌊256·t⌋, 0, 255), `log` и v ≤ 0 — 0; `classes` — k = число границ `breaks` ≤ v (1–255 границ по
возрастанию); `categories` — код, `hidden` — коды, которые не проходят; пусто — 0xFFFF. Нет `from`/`to` — min/max
поля из `stats`.

`POST /viz/blocks/{id}/shell` `{…общие, mode: "shell" | "all", extent: [6] | null, limit: 5000000}` → BFA1
`blocks` (≤ 3 с). `limit` 500 000 – 10 000 000; граней больше — LOD: родители 2^ℓ по осям (среднее по объёму,
мода категорий), `simplified: true`. Заголовок:

```json
{"kind": "blocks", "format": 1, "model": 12, "version": 3, "mode": "shell", "corner": [O], "rotation": 30.0,
 "origin": [75, 50, 25], "faces": 6, "faces_raw": 60000, "simplified": false, "lod": 0, "rows": 1000000,
 "passed": 1000000, "empty": 0, "field": "AU", "colour": {"kind": "scale", "from": 0.1, "to": 10, "log": false},
 "classes": 256, "bins": [[0.1, 0.13875], "…"], "labels": null}
```

Массивы: `c <f4` 3·faces (центр грани в локальных осях минус `origin`), `h <f4` 2·faces (полуразмеры по u, v),
`a <u1` faces (ось·2 + сторона), `k <u2` faces (класс). `origin` — целые, округлённый центр локального охвата;
точка в проекте p = corner + R_z(φ)·(origin + c). `bins` — `[[от, до]]` корзин (`scale`), границы классов
(`classes`: `[[null, b1], [b1, b2], …, [bn, null]]`), null у `none`/`categories`; `labels` — подписи категорий.
Цвет — по 1D-текстуре легенды в интерфейсе: смена цветов не пересылает геометрию; смена поля, `from`/`to`,
`breaks`, `hidden`, фильтра — новый запрос.

`POST /viz/blocks/{id}/slice` `{…общие без colour, field, hidden?, plane: {origin: [x, y, z], normal: [3]},
max_px: 4096}` → BFA1 `blocks_slice` (≤ 1 с). Заголовок `{kind: "blocks_slice", format: 1, model, version,
plane, field, kind: "number" | "category" | null, labels, raster: "cells" | "subcells" | "sampled", width, height,
frame: {origin: [x, y, z] (угол пикселя (0, 0), проект), u: [3], v: [3] (единичные), du, dv}, passed}`. Массивы:
`value <f4` width·height (строка j снизу по v, столбец i по u; категории — код), `m <u1` (0 — нет блока или не
прошёл, 1 — значение, 2 — блок без значения). Плоскость ⟂ оси модели (|n·e| ≥ 1 − 1e−12) — пиксель = ячейка
(`cells`) или подъячейка (`subcells`); иначе `sampled`, du = dv = наименьшая подъячейка; сторона растра > `max_px`
(64–4096) — шаг укрупняется до `max_px`.

`POST /viz/blocks/{id}/at` `{point: [x, y, z], normal: [3] | null, filter_id?, restrict?, fields?: null | [имена]}`
→ JSON (≤ 100 мс): точка сдвигается на −1e−6·(наименьшая подъячейка)·normal внутрь;
`{found: true, row, p, ijk: [i, j, k], sub: [sx, sy, sz, wx, wy, wz] | null, centre: [x, y, z], size: [3] (по осям
модели), passed, f, values: {поле: число | подпись | null}}` или `{found: false}`.

`POST /blocks/{id}/slice/polygons` `{plane, filter_id?, restrict?, field?, limit: 200000}` → `{polygons: [{row,
value, ring: [[x, y, z], …]}], count, area, truncated}` — выпуклые многоугольники плоскость ∩ блок (проект,
против часовой при виде с конца нормали); для печати Э22.

## 13. Фильтры (Д-02) — `objects(kind="filter")`

```
meta v1: {v: 1, rules: Node, text: str | null, fields: [имена], note: str | null}
Node = {op: "and" | "or", items: [Node, …]} | {op: "not", items: [Node]} | {expr: "<8.7.4>"}
     | {cond: {field, op: "=" | "<>" | ">" | ">=" | "<" | "<=" | "empty" | "notempty" | "in", value? | values?}}
     | {wireframe: {id, side: "inside" | "outside", by: "frac" | "centre", m: 4}}
     | {surface: {id, side: "above" | "below", by: "centre" | "frac"}}
```

Глубина ≤ 32, узлов ≤ 500, `values` 1–1000, `expr` ≤ 4000 символов; иначе 422 `filter_rules {param:
"rules.items.0.cond.op"}`. `surface.id` — каркас-поверхность. Доли — ADR §8 (И — произведение, ИЛИ — максимум,
НЕ — 1 − f). `text` — запись 8.7.4 (каноническая: поле — имя или `[имя]`, строки в `'…'` с удвоением кавычки,
целые без точки, иначе кратчайшая запись float с показателем как в JS: `1e-7`; И/ИЛИ — `AND`/`OR` с
пробелами, вложенное ИЛИ в И — в скобках; `empty` → `ISNULL(f)`, `notempty` → `NOT ISNULL(f)`, `in` → `f IN
('A', 'B')`); null, если в дереве есть каркас или поверхность. `title` — описание по-русски всегда.

- `GET /filters` → `{filters: [{id, name, folder, version, text, title, fields, note, created_at, updated_at}]}`.
- `POST /filters` `{name, rules? | expr?, note?, folder?}` → полный фильтр; ровно одно из `rules`, `expr`
  (`expr` → `{expr}`); имя занято — 409 `name_taken`; пусто — 422 `name_empty`; 423.
- `GET /filters/{id}` → `{id, name, folder, version, rules, text, title, fields, note, wireframes: [{id, name,
  available}], created_at, updated_at}`; 404 `filter_not_found`.
- `PUT /filters/{id}` `{expected_version, name?, rules? | expr?, note?}` → полный; 409 `version_conflict
  {version}` (и без `expected_version`); 423.
- `DELETE /filters/{id}` → `{deleted}`; используется — 422 `filter_in_use {usage}`; 423.
- `GET /filters/{id}/usage` → `{usage: [{kind: "object" | "view", id, name, role}]}` — `object_refs(dst=фильтр,
  role="filter")` и сохранённые виды с `block_styles[*].filter_id`.
- `POST /filters/{id}/test` `{model_id}` и `POST /filters/test` `{model_id, rules | expr}` → `{passed, total,
  weight}` (weight = Σf); нет поля — 422 `filter_field_missing {field, model}`.
- Ссылки: `object_refs(src=фильтр, dst=каркас, role="filter_src")`. `.bfggis` — `filters/remap.py` (id каркасов
  в `rules`; нет в архиве — лист «недоступен»: `wireframes[].available: false`, доля 0, предупреждение).

## 14. Вид проекта — `block_styles["<id модели>"]`

```json
{"visible": true, "mode": "shell" | "all", "filter_id": null,
 "quick": {"items": [{"field": "AU", "op": ">", "value": 1}], "join": "and" | "or", "invert": false},
 "colour": {"field": "AU", "kind": "scale", "from": 0.1, "to": 10, "log": false, "palette": "viridis",
            "breaks": [], "colours": [], "categories": {"0": {"colour": "#e07020", "on": true}}},
 "edges": {"on": true, "colour": "#202020", "width": 1}, "opacity": 1.0,
 "slice": {"on": false, "plane": "section" | {"origin": [3], "normal": [3]}}}
```

`quick` → `restrict` тела показа: И/ИЛИ условий `cond`, `invert` — `{op: "not"}`. Удалённый фильтр при чтении вида
— `filter_id: null` (`viewstate/keys.py`, 17.7). Тело стиля проверяет интерфейс (как прочие стили).

## 15. Задания `blocks.*`

| Вид | Очередь | Маршрут | Стадии (`message`) |
|---|---|---|---|
| `import.objects` (блоки) | heavy | `POST /import/objects` | «Чтение», «Сортировка», «Запись» |
| `blocks.prototype` | quick | `POST /blocks/prototype` | «Охват», «Запись» |
| `blocks.fill` | heavy | `POST /blocks/fill` | «Каркасы», «Столбцы», «Граничные родители», «Дробление», «Слияние», «Запись» |
| `blocks.code` | heavy | `POST /blocks/{id}/code` | как `blocks.fill` |
| `blocks.calc` | quick | `POST /blocks/{id}/calc` | «Чтение», «Расчёт», «Запись» |
| `blocks.density`, `blocks.classify` | quick | `POST /blocks/{id}/density`, `/classify` | «Чтение», «Расчёт», «Мажоритарный фильтр» (classify), «Запись» |
| `blocks.regularize` | heavy | `POST /blocks/{id}/regularize` | «Чтение», «Перекрытия», «Запись» |
| `blocks.report` | quick | `POST /blocks/{id}/report` | «Чтение», «Расчёт» |
| `blocks.check` | quick | `POST /blocks/{id}/check` | «Проверки» |
| `blocks.export` | heavy | `POST /blocks/{id}/export` | «Чтение», «Запись» |

Запись — последней стадией, целиком или ничего (временные файлы → фиксация); отмена — между порциями; итог —
побитно одинаков при 1, 2, 4, 8 потоках. Строки оркестратора: виды — `jobs/registry.py`; прогрев `blocks.warm` —
`ops/warm.py`.

## 16. Команды → маршруты

`blocks.show`, `blocks.settings` — интерфейс (вид `block_styles`, затем `/viz/blocks/{id}/shell`);
`blocks.filter` → `block_styles[id].filter_id` + показ; `blocks.slice` → `/viz/blocks/{id}/slice`;
`blocks.prototype` → `/blocks/prototype[/preview]`; `blocks.fill` → `/blocks/fill`; `blocks.code` →
`/blocks/{id}/code`; `blocks.calc` → `/blocks/calc/check` + `/blocks/{id}/calc`; `blocks.density`, `.classify`,
`.regularize`, `.report`, `.export`, `.check` → `/blocks/{id}/<то же>`; `filters.manage` — окно «Фильтры»;
`filter.save` → `POST` / `PUT /filters`; `filter.delete` → `DELETE /filters/{id}`; `filter.test` →
`/filters/{id}/test`; `import.files {kind: "blocks"}` — мастер импорта.

## 17. Фикстуры

- `grids.json` — сетки v1 и v2 (`GridV2.of` → `to_meta`, `cells`, `levels`, `subcell`, `lengths`, `matrix`,
  `extent`, точки локальные → проекта), перевод v1 → v2 (`upgrade`, `public_meta`), отказы (`rotation_unsupported`,
  `subblock_div`, `variable_grid`, `block_model_too_large`) телами ответа.
- `contract_answers.json` — настоящие ответы маршрутов Э11 (`list`, `get`, `patch`, `patch_conflict`,
  `not_found`, `delete`) и ожидаемый ответ v2 (`get_v2` = строка + `public_meta` той же модели; `get_v2_sub` —
  модель φ = 30°, `div` [8, 8, 4], поле `fld:`), по которому 17.3 сверяет `store`.
- Дальше: `expr_texts.json` (17.5), `viz_answers.json` (17.27, `BF_REGEN_BLOCKS_VIZ=1`), фикстуры окон (17.28,
  17.29).
