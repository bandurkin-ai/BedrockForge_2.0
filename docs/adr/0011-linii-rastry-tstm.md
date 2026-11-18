# ADR 0011. Линии, точки, растры, ЦТМ; SHP, MIF/MID, канавы (Э7)

**Статус:** Принято, 2026-11-17 (Э7; проект — architect на opus / xhigh — лимит Fable; принято оркестратором: перенос основы каркасов и «Построить ЦТМ» в Э7 — без него 7.20 и Д-07 не сдаются; Э8 и Э9 уменьшаются на столько же). Не пересматриваются: ADR 0003 (схема), 0004 (3D: части, LOD, бюджеты), 0007 (BFA1, `Scene.addLayer`, отсечение), 0009 (pyshp, свой MIF, tifffile, imagecodecs по образцам, DWG — Э8).

## Контекст

- ТЗ 7.18–7.20, 9.3, 9.5–9.7, 9.12 (Э7: STR Micromine и Surpac — импорт и экспорт, DXF — линии, точки и текст, точки XYZ, растры и GeoTIFF-экспорт); 7.35: Д-07 п. 2, Д-13 (SHP, MIF/MID, GeoTIFF, `.PIT`), Д-16 (канавы). В ТЗ (раздел 11) на Э7 — 2 окна, без Д-13 и Д-16.
- В ядре BF2 есть только DAT-ридер и DAT-писатель (`formats/micromine*.py`), CSV/Excel, `crs.py` (СК — метка, без перепроецирования), `storage/blobs.py`. Модули `lines`, `geo`, `exporting` пустые. В интерфейсе объявлены `lines.upload`, `rasters.upload`, `lines.export`, `raster.set`, `raster.place_on_section`, `wireframe.dtm`, `object.select` (Э7), `model.dtm` (Э9).
- В BF (`claude/laughing-bohr-gpy965`) есть: `formats/lines.py` (238 строк), `rasters.py` (340), `export.py` (`write_micromine_str` побитно, `write_dxf_lines`), `dxf_stream.py` (290), `geo/meshes.py` (части, Мортон, LOD), `geo/surfaces.py::build_dtm` (PythonCDT), `models.py` (`Wireframe.dtm`, `LineLayer`, `Raster`), `scene3d.ts` (строки 1483–1586 драпировка, 1587–1659 линии, 1812–1893 растры), тесты `test_lines_rasters.py`, `drape.test.ts`.
- **Главная находка.** По 7.20 ЦТМ — это каркас с признаком, а каркасы (хранение, части, LOD) появляются только на Э8. «Построить ЦТМ» по 7.23 — Э9. Без основы каркасов приёмку 7.20 («растр повторяет рельеф на всех LOD») и Д-07 (E2E строк ЦТМ) на Э7 не сдать.
- Образцы в `tests/data/real/`: `Без имени.STR`, `Восточный Цители-Сопели.STR`, `TST-FAULTS.DXF`. Растров с привязкой нет («Восточный_TS» в git не попал).

## Варианты

| Вопрос | Варианты | Выбор и причина |
|---|---|---|
| ЦТМ раньше каркасов | А) основа каркасов (хранение, части, LOD, слой 3D) и «Построить ЦТМ» из точек и линий — в Э7; Б) в Э7 только контракт, а 7.20 и Д-07 п. 2 — в конце Э8; В) ЦТМ как отдельный вид объекта (сетка высот) | **А.** 7.20 и Д-07 закрываются в своём этапе. Работа не дублируется: она переносится из Э8 и Э9, а Э8 добавит читатели TRIDB/DXF/OBJ/STL/DTM, проверки, объём, BVH и сечения. Б переносит обязательный пункт владельца без его согласия. В противоречит 7.20 («объект остаётся каркасом») |
| Хранение линий | А) JSON в БД (BF `line_attributes`); Б) массивы в blobs, атрибуты в Parquet-blob, схема в БД, показ — BFA1 частями; В) строка БД на вершину | **Б.** 1 млн точек SHP/XYZ в JSON не укладывается в 10 с. Неизменяемые blobs — готовая основа для версий редактора (Э9). Строки БД — 1 млн вставок на каждый импорт |
| DXF | А) `ezdxf.recover` целиком (BF); Б) свой потоковый разбор ASCII; `ezdxf` — для `recover` двоичных и битых файлов, для математики кривых и для записи | **Б.** Так требует 9.12; разбор общий с каркасами Э8 (перенос `dxf_stream.py`) |
| Пиксели растров | А) только Pillow; Б) tifffile (теги GeoTIFF, полосы без сжатия) + Pillow (LZW, JPEG, Deflate через встроенный libtiff); В) Б + imagecodecs | **Б.** Новых пакетов нет. В — только если образцы владельца не прочтутся (условие ADR 0009) |
| Траектория канавы | А) инклинометрия az/dip по вершинам (касательная); Б) станции с координатами xyz; В) отдельный blob полилиний набора | **Б.** Позиции на вершинах — ровно сохранённые float64 (≤ 1e−9 м). В А сумма приращений на UTM (шаг float64 около 9e−10 м при 5e6 м) выходит за 1e−9. В В пришлось бы переписать экспорт, аудит и `.bfggis` |

## Решение

### 1. Хранение (миграция `0005_e7_lines_rasters_dtm`, `archive/spec.py`, владелец blobs `object`)

| Объект | БД (1 : 1 к `objects`) | Blobs (роли) | Заметки |
|---|---|---|---|
| Файл линий `objects(kind=line_file)` | `meta`: формат, кодировка, схема полей исходника (имя, тип, ширина, знаки, описание, порядок), СК, предупреждения | `source` | Родитель слоёв; удаление — со слоями |
| Слой `objects(kind=line_layer)` → `line_layers` | `line_count`, `point_count`, `length`, `closed_count`, `flat`, `color`, `attr_schema` (имя, тип, единица), `points_only`, `origin` | `xyz <f8 N×3`, `offsets <i8`, `closed u1`, `seq <u4` (порядок записей в файле), `attrs` (Parquet, строка на линию), `vattrs` (Parquet, строка на вершину — только поля, которые меняются вдоль линии: M в SHP, описания Surpac) | Слой = код `STRING` / номер Surpac / слой DXF / SHP / MIF. Замкнутая линия повторяет первую вершину в конце (17, п. 12). У точек `offsets` и `closed` не хранятся. Текст DXF/MIF — точка с атрибутами `text`, `height`, `rotation`. Пустое — NaN/null, не 0. Версии слоя (`line_layer_versions`) — Э9 |
| Растр `objects(kind=raster)` → `rasters` | `width`, `height`, `georef` (affine 6, `affine_z` или null, источник `geotiff/world/tab/grf/section/manual`, СК, пиксель, поворот, `corners3d` f64 4×3, отклонения по опорным точкам остальных файлов), `elevation` (null — под самым низким объектом), `vertical`, `opacity` 0,05–1, `version` | `source`, `sidecar:<ext>`, `display0` (≤ 4096 px), `display1` (≤ 1024 px) | Сдвиг по Z меняет `corners3d` и `elevation` |
| Каркас `objects(kind=wireframe)` → `wireframes` | `point_count`, `triangle_count`, `origin`, `stats`, `render` (части, LOD), `attrs` (атрибуты пользователя, 7.20), `dtm` — null или `{clip, tint, opacity, shade}` | `xyz <f8`, `tri <i4`; части и LOD 1/8, 1/64 — в `cache\mesh\` | Растр на ЦТМ — `object_refs(src=каркас, dst=растр, role=drape)`, поэтому `.bfggis` перенумерует ссылку сам. Удалить используемый растр нельзя: 422 со списком ЦТМ |
| Канава | `dh_surveys` + `x`, `y`, `z` (Double, nullable) | — | Станции с xyz имеют приоритет над методом десуртинга |

### 2. Форматы (`core/bf_core/formats/`, каждый файл ≤ 600 строк; правила 9.12: потоково, прогресс, отмена, «бомбы», журнал операции)

| Формат | Чтение | Запись | Перенос из BF / основа |
|---|---|---|---|
| Micromine STR, `.PIT` | DAT-ридер Э3; новая линия при смене `JOIN`/`STRING`; свои поля — атрибуты; `Road` — атрибут | Побитно: заголовок и схема полей исходника, `seq` | `lines.py::read_micromine_str`, `export.py::write_micromine_str` |
| Surpac STR (текст) | `номер, Y, X, Z, D1…`; нулевая запись — конец сегмента; `0,…,END`; cp1251 | Да: координаты через `repr` (цикл побитный) | `read_surpac_str` + своя запись |
| DXF | Поток ASCII: LINE, LWPOLYLINE (bulge), POLYLINE 2D/3D, POINT, TEXT/MTEXT, ARC, CIRCLE, ELLIPSE, SPLINE → ломаные (хорда ≤ 0,1 м, `ezdxf.math`); OCS → WCS; `$DWGCODEPAGE`; двоичный и битый — `ezdxf.recover`. Сетки (POLYFACE, 3DFACE, MESH) — в составе как «каркасы — этап Э8» | Линии — 3D POLYLINE, точки — POINT; `repr`, ACI, CRLF | `dxf_stream.py`, `read_dxf_lines`, `write_dxf_lines` |
| SHP | pyshp: Point/MultiPoint → точки; PolyLine → линии; Polygon → замкнутые линии, кольца — атрибут «кольцо»; Z, M (`vattrs`); DBF; `.cpg`, иначе cp1251 с предупреждением; `.prj` → `crs.py`. Если замер не уложится — быстрый путь numpy для Point и колонок DBF | — | ADR 0009 |
| MIF/MID | Свой разбор: Point, Line, Pline (Multiple), Region, Text; `Delimiter`, `Charset`; `CoordSys Earth Projection` → WKT по таблице (долгота/широта, поперечная Меркатора/UTM, ГК на Пулково-42, WGS 84); иная или `NonEarth` — без СК с предупреждением | — | ADR 0009 |
| Точки XYZ (DAT/CSV) | Табличные ридеры Э3 + сопоставление X/Y/Z/имя | DAT (`micromine_write.py`), CSV | — |
| Привязка | GeoTIFF → world → TAB → GRF; отклонения по каждой точке; вертикальный растр — GRF с разными Z или по линии разреза | GeoTIFF: tifffile, `ModelPixelScale` + `ModelTiepoint` или `ModelTransformation`, GeoKeys (EPSG из СК) | `rasters.py` целиком |
| Изображения | Размер — до распаковки (≤ 400 Мпикс, иначе отказ); копии 4096 и 1024 px (`draft`/`reduce`, по полосам); 16 бит и float → 8 бит растяжкой p2–p98 | Исходный файл | `_display_copy` |

### 3. API и задания (префикс `/api/v1`; ошибки `{code, message, hint}`; 409 по `expected_version`)

| Запрос / задание | Назначение |
|---|---|
| `POST /import/objects/inspect`, задание `import.objects` | Состав по файлам: слои, линии, вершины, растры и их привязка, что не загрузится и почему; блок «Уровень растров по Z» (`elevation`, `shift`). Объекты пишутся в `building`, в конце — `ready` и `source` одной транзакцией; отмена не оставляет объектов; без промежуточной копии файла |
| `GET/PATCH /lines/layers/{id}`, `GET /lines/layers/{id}/attrs?offset&limit` | Свойства (цвет, имя, длина, замкнутость), атрибуты страницами |
| `GET /viz/lines/{id}`, `/viz/lines/{id}/parts/{k}?lod=0,1,2` | BFA1 `lines`: `offsets u4`, `xyz f4×3` от начала части, `line u4`, `closed u1`; у точек — без `offsets`; подписи текста — в заголовке (≤ 5000 на часть). Части: Мортон, ≤ 256 тыс. вершин, целое начало; LOD — Дуглас–Пекер 4 и 32 м (у точек — одна на ячейку); ETag — по ADR 0007 |
| `GET/PATCH /rasters/{id}`, `POST /rasters/{id}/shift {dz}`, `POST /rasters/{id}/place-on-section {z_top}`, `GET /rasters/{id}/image?level=` | `raster.set`; сдвиг всех углов (растр без высоты → 422 `no_elevation`); `raster.place_on_section {raster_id, z_top}` по линии разреза из настроек вида; изображение с ETag/304 |
| `GET /viz/wireframes/{id}`, `/parts/{k}?lod=` | BFA1 `mesh`: `xyz f4×3`, `idx u2/u4`; части ≤ 64 тыс. треугольников (ADR 0004) |
| `PUT /wireframes/{id}/dtm`, `PATCH /wireframes/{id}/attrs` | `wireframe.dtm {wireframe_id, on, raster_id, clip, tint, opacity, shade}`; `raster_id: 0` снимает растр; вертикальный → 422; «вернуть в каркасы» сбрасывает настройки и связь; атрибуты: добавить, изменить, переименовать, удалить |
| Задания `export.lines` (STR, Surpac, DXF; CSV/DAT для точек), `export.raster` (исходник или GeoTIFF), `model.dtm`, `import.trenches` | Журнал операции и `export_log`; `model.dtm` — перенос `build_dtm` (Делоне с ограничениями, PythonCDT): точки, линии, устья, структурные линии, граница, `max_edge`; результат сразу в раздел ЦТМ |

### 4. Слои 3D (через `Scene.addLayer`; материалы с `scene.clipPlanes`; Worker; учёт в `viz/core/{cull,lod,budget}`)

| Слой | Решение |
|---|---|
| Линии и точки | `LineSegments2`, при ≤ 1 px или > 1 млн отрезков — `LineSegments` (правило ADR 0007); точки — значок шейдером; тексты — общие подписи; отсекаются; LOD с гистерезисом 20 %, помета «упрощено». Выбор `object.select` — ближайший отрезок ≤ 6 px или точка ≤ 8 px на CPU, ≤ 50 мс |
| Растр в плане | Четырёхугольник по `corners3d` от целого начала; текстура `display0` или `display1` — по метрам на пиксель и бюджету видеопамяти; отсекается |
| Вертикальный растр | Без отсечения — обычный объект; при отсечении — подложка: вне `clipPlanes`, рисуется первым, без записи и проверки глубины, поэтому скважины поверх |
| Каркас и ЦТМ | Части, LOD 1 / 1/8 / 1/64, `flatShading`. Драпировка — перенос `drapeMaterial` и `imageAxes`: UV считаются в шейдере из плановых XY вершины, поэтому одинаковы на любом LOD; тон — множитель, непрозрачность — смешение, тени — освещение; «Скрыть данные за ЦТМ» — как в BF `applyDrape`. Точный выбор и сечения — Э8 |

### 5. Интерфейс и команды

| Что | Решение |
|---|---|
| Мастер «Импорт файлов» | Новые расширения `.str .pit .dxf .shp .dbf .shx .prj .cpg .mif .mid` + растры и файлы привязки; `.str` — по содержимому; «Уровень растров по Z» (запятая и точка) |
| Канавы (Д-16) | Шаг 1 мастера скважин — «Канавы по полилиниям»; «Главная › Загрузка › Канавы» (`import.open {mode: "trenches"}`); «+» у «Скважин» → «Импорт канав…». Источник — STR, DXF или CSV вершин; имя — из стринга/`JOIN`/слоя или из поля; «Обратить направление»; пробы — как в 7.7. Устье — первая вершина, длина — 3D-длина, «от/до» — по 3D-длине. Аудит инклинометрии к станциям с xyz не применяется |
| Дерево | «Линии» (файлы → слои), «Растры», «Каркасы» (минимум: видимость, цвет, значок и меню «Перенести в ЦТМ»), «ЦТМ» — **без шестерёнки** (Д-07): щелчок открывает настройки вверху «Свойств», в меню — «Настройки ЦТМ…» и «Вернуть в каркасы». Ключи видимости — `ln:`, `rs:`, `wf:` (ADR 0007) |
| Свойства | Слой: цвет, видимость, длина, замкнутость, атрибуты (выбранная линия — её значения). Растр: «Высота», «Сдвиг по Z» и «Сдвинуть» (без высоты недоступно, с причиной), непрозрачность, «Левый/Правый край X/Y», «Верх/Низ Z», «На текущий разрез» (своё окно подтверждения), отклонения. ЦТМ: растр (вертикальные не предлагаются), «Скрыть данные за ЦТМ», цвет, непрозрачность, тени, атрибуты |
| Реестр | Уточняются параметры: `raster.set {raster_id, elevation, opacity}`, `raster.place_on_section {raster_id, z_top}`, `wireframe.dtm` (полный список), `lines.export {format: str/surpac/dxf/csv/dat}`. Новые: `rasters.export`. Обработчики получают `model.dtm` (перенос с Э9) и `object.select`. У каждой команды — кнопка ленты |
| Демо | Рельеф (горизонтали и точки) → ЦТМ, ортофото с JGW, вертикальный скан с GRF, 5 канав с пробами (`demo/project.py PARTS`) |

### 6. Зависимости

| Пакет | Решение |
|---|---|
| **pyshp** (MIT, чистый Python, `py3-none-any`, около 75 КБ) | Новая; дополнение ADR 0001 в подшаге 7.7. Кандидат — 3.1.7 (2026-11-07; последний до `exclude-newer` 2026-11-09, не `.0`); точная версия — `uv lock` по правилам ADR 0001; `check_licenses_py.py` |
| imagecodecs (BSD-3, 18 МБ) | **Не добавляется**: Pillow (уже в стеке) читает TIFF с LZW, JPEG и Deflate, tifffile — теги. Пересмотр — только по образцам владельца, которые не прочтутся (BigTIFF + LZW, LERC, ZSTD) |
| Остальное | Уже в стеке: ezdxf 1.4.5, tifffile, pillow 12.3.0, pyproj 3.8.0, pyarrow, numba, pythoncdt 2.0.2, pyfqmr 0.5.0; в интерфейсе — three 0.170.0, three-mesh-bvh |

## Риски

| Риск | Мера |
|---|---|
| Объём этапа: 33 подшага против 2 окон в ТЗ | Сначала ядро форматов (оно нужно и Э8); каждый подшаг даёт рабочий срез; Э8 и Э9 уменьшаются на перенесённое |
| Схема каркасов задана до требований Э8 | Перенос модели BF `Wireframe` и правил ADR 0004; Э8 добавляет только колонки (атрибуты TRIDB, проверки) |
| Побитный STR на реальных файлах (ширина полей, суффикс ICU, NUL до 4096, поля N) | Перенос писателя BF; тест байт в байт на двух файлах `real_data` |
| Разнообразие DXF (bulge, OCS, кодовая страница, двоичный, SPLINE) | Свой разбор + `recover`; синтетика через ezdxf; `TST-FAULTS.DXF` |
| pyshp медленный на 1 млн точек или записей DBF (цикл Python) | Замер в 7.7; быстрый путь numpy для Point и DBF (записи фиксированной длины) |
| `CoordSys` MapInfo не разбирает pyproj | Таблица распространённых проекций; иное — «СК не распознана», объект загружается без СК |
| Память: растры до 400 Мпикс, текстуры на встроенной графике | Проверка размера до распаковки, копии по полосам; два уровня текстур в менеджере бюджета |
| Pillow не прочтёт часть GeoTIFF (BigTIFF, тайлы, 16 бит) | Проверка в 7.11 на синтетике tifffile; если не прочтётся — imagecodecs по ADR 0009 |
| Драпировка и подложка под SwiftShader в E2E | Малые сцены; проверка пикселей при двух уровнях LOD; ожидание по событию кадра |

## Замеры (`bench/scenarios/`; ci ≈ 1/10 от full)

| Точка | ci / full | Бюджет | Где |
|---|---|---|---|
| Импорт SHP и MIF/MID (Д-13) | 100 тыс. / 1 млн вершин | ≤ 1,5 с / ≤ 10 с; ≤ 1 ГБ | `lines_import.py` |
| Импорт STR Micromine и DXF; экспорт STR | 100 тыс. / 1 млн вершин | STR ≤ 10 с, DXF ≤ 15 с, экспорт ≤ 5 с (предложение; уточняется первым замером); ≤ 1 ГБ | `lines_import.py` |
| Канавы (Д-16) | 1000 / 10 000 × 50 вершин | ≤ 1,5 с / ≤ 10 с | `trenches.py` |
| Части и LOD линий | 100 тыс. / 1 млн вершин | ≤ 0,5 с / ≤ 2 с; повтор — 304 | `lines_parts.py` |
| Импорт растра | 10 / 100 Мпикс JPEG + JGW | ≤ 3 с / ≤ 15 с; ≤ 1,5 ГБ (предложение); 401 Мпикс → отказ ≤ 1 с без выделения памяти | `raster_import.py` |
| «Построить ЦТМ»; первый кадр ЦТМ | 100 тыс. / 1 млн точек | Бюджет — по первому замеру (ориентир ≤ 30 с); первый кадр 1 млн треугольников ≤ 3 с (5.3) | `dtm_build.py`, E2E |
| Кадр под программным рендером | Демо + 1 млн отрезков + растр + ЦТМ 200 тыс. треугольников | Вызовов отрисовки ≤ 40, треугольников ≤ 1,5 млн, видеопамяти ≤ 300 МБ; мс — в отчёт | `perf3d.spec.ts` |
| Клик по линии или растру | Демо | ≤ 50 мс (5.3) | E2E |

## План подшагов (по 30–45 мин; после каждого — чекпойнт по CLAUDE.md; одновременно не больше 2 субагентов)

| № | Подшаг | Файлы | Проверка | Кто |
|---|---|---|---|---|
| 7.0 | ADR → «Принято», план в PROGRESS, напоминание владельцу (№ 4) | `docs/adr/0011-…`, `PROGRESS.md` | — | оркестратор |
| 7.1 | Миграция 0005, роли blobs, архив | `db/schema.py`, `migrations/project/versions/0005_e7_lines_rasters_dtm.py`, `archive/spec.py` | pytest: цикл миграций (SQLite, `-m db`), `.bfggis` с `drape` | оркестратор |
| 7.2 | Модель и хранилище линий | `formats/lineset.py`, `lines/store.py` | pytest: NaN, точки без `offsets`, повтор первой вершины | kernel-engineer |
| 7.3 | Micromine STR и `.PIT` | `formats/str_micromine.py` | pytest: 2 файла `real_data` байт в байт; негативные | kernel-engineer |
| 7.4 | Surpac STR: чтение и запись | `formats/str_surpac.py` | pytest: цикл побитно; сегменты, END | kernel-engineer |
| 7.5 | DXF поток: токенизатор, простые сущности, OCS, текст | `formats/dxf_stream.py`, `formats/dxf_lines.py` | pytest: синтетика, `TST-FAULTS.DXF`, память на 1 млн вершин | kernel-engineer |
| 7.6 | DXF кривые, `recover`, запись | `formats/dxf_curves.py`, `formats/dxf_write.py` | pytest: хорда ≤ 0,1 м; цикл побитно | kernel-engineer |
| 7.7 | SHP: дополнение ADR 0001, `uv lock`, читатель | `core/pyproject.toml`, `uv.lock`, `formats/shp.py` | pytest + негативные (ADR 0009); `check_licenses_py.py` | kernel-engineer |
| 7.8 | MIF/MID и `CoordSys` | `formats/mif.py`, `formats/mif_crs.py` | pytest: все типы, UTM/ГК, неизвестная СК | kernel-engineer |
| 7.9 | Точки XYZ; реестр объектных форматов | `formats/points.py`, `formats/registry.py` | pytest: цикл 1e−12; `.str` по содержимому | kernel-engineer |
| 7.10 | Привязка растров (перенос `rasters.py`) | `formats/georef.py` | pytest: JGW = центр пикселя (1e−9); TFW+TAB; вертикальный GRF | kernel-engineer |
| 7.11 | Изображения, «бомбы», GeoTIFF-запись | `formats/raster_image.py`, `formats/geotiff_write.py` | pytest: 401 Мпикс, LZW/JPEG/тайлы; углы после цикла 1e−9 | kernel-engineer |
| 7.12 | `import.objects` + inspect | `importing/objects.py`, `importing/router.py`, `lines/service.py`, `rasters/store.py` | pytest: отмена без объектов, журнал | оркестратор |
| 7.13 | API линий и растров | `lines/router.py`, `rasters/router.py`, `api/modules.py` | pytest: 409, сдвиг −5, 422 `no_elevation`, 304 | оркестратор |
| 7.14 | Части и LOD линий, BFA1 `lines` | `viz/lines.py`, `geo/simplify.py` | pytest: отклонение ≤ tol, целое начало, ETag | kernel-engineer |
| 7.15 | Экспорт линий, точек, растров | `lines/export.py`, `rasters/export.py` | pytest: импорт → экспорт → импорт побитно | оркестратор |
| 7.16 | Станции с xyz в десуртинге; исключения аудита | `drillholes/{desurvey,trajectory}.py`, `audit/rules.py` | pytest Д-16: 5 вершин ≤ 1e−9 м, нет «излома» | kernel-engineer |
| 7.17 | `import.trenches` + замер | `importing/trenches.py`, `bench/scenarios/trenches.py` | pytest: имя, «Обратить», пробы; 10 000 × 50 | kernel-engineer |
| 7.18 | Основа каркасов: хранение, части, LOD (перенос `meshes.py`), BFA1 `mesh` | `wireframes/store.py`, `geo/meshes.py`, `viz/meshes.py` | pytest: части, u2/u4, ETag | kernel-engineer |
| 7.19 | «Построить ЦТМ» (перенос `build_dtm`), задание `model.dtm` | `geo/dtm.py`, `wireframes/jobs.py` | pytest: значения в узлах (допуск 0), структурные рёбра | kernel-engineer |
| 7.20 | API ЦТМ и атрибутов | `wireframes/router.py` | pytest: вертикальный → 422, «вернуть» сбрасывает, `.bfggis` | оркестратор |
| 7.21 | Мастер: объекты, состав, «Уровень растров по Z» | `modules/importing/{ObjectsStep,RasterZLevel}.tsx`, `wizard.ts` | vitest: числа с запятой; E2E: импорт STR | оркестратор |
| 7.22 | Мастер «Канавы по полилиниям», лента, «+» | `modules/importing/TrenchesStep.tsx`, `shell/ribbonConfig.ts` | vitest | оркестратор |
| 7.23 | Дерево: «Линии», «Растры», «Каркасы», «ЦТМ» без шестерёнки | `modules/{lines,rasters,dtm}/*Tree.tsx`, `shell/DataTree.tsx` | vitest; E2E Д-07 | оркестратор |
| 7.24 | Слой линий и точек, подписи, выбор | `viz/lines/{layer,build,pick}.ts`, `viz/worker/lines.ts` | vitest: сборка, выбор; E2E кадр | оркестратор |
| 7.25 | Растры в 3D: план, вертикальный, уровни текстур | `viz/rasters/{layer,placement}.ts` | vitest углов; E2E: JGW по пикселю, скважины поверх подложки | оркестратор |
| 7.26 | Поверхности и драпировка | `viz/mesh/{layer,drape}.ts`, `viz/worker/mesh.ts` | vitest (перенос `drape.test.ts`); E2E: два уровня LOD | оркестратор |
| 7.27 | «Свойства» слоя, растра, ЦТМ | `modules/{lines,rasters,dtm}/*Props.tsx` | vitest; E2E: «Сдвинуть», «На текущий разрез» | оркестратор |
| 7.28 | Команды, лента, окна экспорта | `modules/*/commands.ts`, `shell/ribbonConfig.ts` | vitest реестра: у каждой команды кнопка | оркестратор |
| 7.29 | Демо и E2E | `demo/{project,geo}.py`, `app/e2e/{lines,rasters,dtm,trenches}.spec.ts` | E2E на Linux; `windows.yml` | оркестратор |
| 7.30 | Замеры и бюджеты | `bench/scenarios/*.py`, `bench/budgets.json`, `perf3d.spec.ts` | `bench.run --check` | оркестратор |
| 7.31 | Ревью и исправления (1–2 подшага) | — | полный набор тестов | reviewer (opus / high) |
| 7.32 | Документация и PR «Э7. Линии, растры, ЦТМ; SHP, MIF/MID, канавы» | `USER_GUIDE.md`, `CHECKLIST.md`, `CHANGELOG` | CI `ci` и `windows` | docs-writer + оркестратор |

Порядок: 7.1 ∥ 7.2–7.4 → 7.5–7.11 (форматы) ∥ 7.12–7.13 → 7.14–7.20 → интерфейс 7.21–7.28 → 7.29–7.32. Оценка — **4–5 окон по 5 ч** (в ТЗ — 2): +1 окно за Д-13 и Д-16, +1 — за основу каркасов и «Построить ЦТМ», перенесённые с Э8 и Э9.

## Вопросы владельцу (блокирующих нет)

| № | Вопрос |
|---|---|
| 1 | Напоминание (7.35.6 № 4): образцы Surpac двоичных STR/DTM, Datamine, Vulcan, сжатых GeoTIFF; какие форматы Micromine ещё нужны; векторный TAB |
| 2 | Растр с привязкой «Восточный_TS» (JPG + JGW + TAB + GRF) и вертикальный скан с GRF — сейчас приёмка 7.19 идёт на синтетике |
| 3 | В каких СК и кодировках ваши SHP и MIF (Пулково-42 ГК, МСК, UTM)? Это нужно для таблицы `CoordSys` |
| 4 | К сведению: Э7 растёт до 4–5 окон, потому что основа каркасов и «Построить ЦТМ» переносятся с Э8 и Э9 ради приёмки 7.20 и Д-07. Есть возражения? |

## Последствия

- Э8 добавляет к готовой основе читатели каркасов, проверки, объём, BVH, точный выбор и сечения; Э9 — версии слоя (`line_layer_versions`), редактор и остальные операции 7.23. Изолинии, объём и обрезка ЦТМ появятся вместе с ними.
- Д-17 (Э11) получает писателей: линии — STR, Surpac, DXF; точки — DAT, CSV; растры — исходник или GeoTIFF.
- Тайлы растров больше 4096 px в v1 не делаются (7.19); если понадобится — `cache\` по ADR 0002.
