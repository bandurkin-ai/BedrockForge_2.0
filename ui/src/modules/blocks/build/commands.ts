/**
 * The commands of the building of a block model (ТЗ 8.7.3, 8.7.5, 8.7.7; ADR 0023 §6, §9, §13): `blocks.prototype`,
 * `blocks.fill`, `blocks.code`, `blocks.regularize`, `blocks.rebuild`. The ribbon (the tab «Блочные модели», the groups
 * «Создание» and «Поля»), Ctrl+K, the keys of the user and the assistant run the same commands. Without arguments a
 * command opens its window (as ADR 0021 п. 9); with them it checks them and starts the job — the parameters are flat
 * (`ParamSchema` has no lists): the lists are text («10; 10; 5», «3, 7»), the wireframes — JSON
 * `[{"id": 5, "code": "Руда", "priority": 2}, {"id": 6}]` or numbers «5; 6». All of them change stored data: a call of
 * the assistant waits for the confirmation of the user (`confirm`). `CREATE_BUTTONS` / `FIELD_BUTTONS` — the buttons of
 * the ribbon (`shell/ribbonConfig.ts` puts them in, by the orchestrator).
 */
import type { Command, ParamSchema } from "../../../shell/commands";
import type { IconName } from "../../../shell/Icon";

const P = (type: ParamSchema["type"], description: string, extra: Partial<ParamSchema> = {}): ParamSchema => ({ type, description, ...extra });

const WIRES = P("string", 'каркасы: JSON-список [{"id": 5, "code": "Руда", "priority": 2}, {"id": 6}] или номера через «;». Больше приоритет — главнее; равные — по порядку списка; код по умолчанию — имя каркаса');
const MODE = P("string", "что кодируется: inside — внутри каркасов (по умолчанию), outside — снаружи (код «вне»), both — и то и другое", { enum: ["inside", "outside", "both"] });

export const commands: Command[] = [
  {
    id: "blocks.prototype",
    title: "Прототип модели",
    description:
      "Создаёт прототип блочной модели (8.7.2): сетку родительских блоков без строк (число родителей ≤ 2·10⁹). Охват — объекты (object_ids, их габариты) или границы (extent) плюс отступ pad; начало сетки кратно размеру блока " +
      "в повёрнутых осях (поворот только вокруг Z). levels — уровни субблоков L по осям (0–7; блок делится пополам, родитель на 2^L). all=true — «Все родители заняты» (в модели строка на каждого родителя). " +
      "Без аргументов открывает окно с рамкой сетки в 3D, числом родителей и объёмом на диске. Прототип затем заполняют каркасами (blocks.fill).",
    params: {
      size: P("string", "размер блока X; Y; Z в метрах (одно число — для всех осей), например 10; 10; 5"),
      object_ids: P("string", "номера объектов для охвата через запятую (каркасы, линии); вместо extent"),
      extent: P("string", "границы вместо объектов: xmin; ymin; zmin; xmax; ymax; zmax"),
      pad: P("string", "отступ от охвата, м: одно число или X; Y; Z (по умолчанию 0)"),
      rotation: P("number", "поворот вокруг Z в градусах, от −180 (не включая) до 180, против часовой стрелки при виде сверху (по умолчанию 0)"),
      levels: P("string", "уровни субблоков L по осям X; Y; Z, каждый 0–7 (по умолчанию 0; одно число — для всех осей)"),
      all: P("boolean", "«Все родители заняты»: в модели строка на каждого родителя (по умолчанию нет)"),
      name: P("string", "имя модели (по умолчанию «Прототип»)"),
      folder: P("string", "папка проекта для модели"),
    },
    confirm: true,
  },
  {
    id: "blocks.fill",
    title: "Заполнить каркасами",
    description:
      "Заполняет прототип блочной модели каркасами (8.7.3): новая модель с полями CODE (код тела) и FRAC (доля объёма блока в теле). Каркасы должны быть замкнутыми и согласованными. Граничные блоки делятся на субблоки по уровням levels " +
      "(по умолчанию — уровни прототипа), доля FRAC — по m×m×m точкам подсетки. Несколько каркасов — по приоритетам; режим inside / outside / both. Без аргументов открывает окно. Ход: «Каркасы», «Столбцы», «Граничные родители», " +
      "«Дробление», «Слияние», «Запись»; отмена ничего не записывает. Если каркас не пересекает сетку в плане — предупреждение в ответе.",
    params: {
      prototype_id: P("integer", "номер прототипа (раздел «Блочные модели» дерева; модель без строк, созданная командой «Прототип»)"),
      wireframes: WIRES,
      mode: MODE,
      levels: P("string", "уровни дробления L по осям X; Y; Z (0–7; по умолчанию — уровни прототипа)"),
      m: P("integer", "точек подсетки m по оси для доли объёма, 1–16 (по умолчанию 4)"),
      outside_code: P("string", "код блоков снаружи каркасов при режиме outside / both (по умолчанию «вне»)"),
      code_field: P("string", "имя поля кода (по умолчанию CODE)"),
      frac_field: P("string", "имя поля доли объёма (по умолчанию FRAC)"),
      name: P("string", "имя модели (по умолчанию «‹прототип› (заполнение)»)"),
      folder: P("string", "папка проекта для модели"),
    },
    confirm: true,
  },
  {
    id: "blocks.code",
    title: "Кодировать каркасами",
    description:
      "Кодирует блоки готовой модели каркасами (8.7.3). По центру (split=false, по умолчанию): новое поле кода в той же модели, субблоки не меняются; поле доли объёма FRAC — только если задан frac_field. С дроблением (split=true): " +
      "новая модель «‹имя› (кодирование)», граничные блоки делятся, потомки наследуют поля родителя, поля CODE и FRAC. Занятое имя поля — 409 field_exists; второе поле доли — frac_exists; модель изменили — version_conflict " +
      "(перечитайте). Без аргументов открывает окно. Ход: «Каркасы», «Граничные родители», «Дробление», «Запись».",
    params: {
      model_id: P("integer", "номер блочной модели (с блоками)"),
      wireframes: WIRES,
      mode: MODE,
      split: P("boolean", "true — с дроблением граничных блоков (новая модель); false — по центру (поля той же модели; по умолчанию)"),
      m: P("integer", "точек подсетки m для доли объёма при дроблении, 1–16 (по умолчанию 4)"),
      outside_code: P("string", "код блоков снаружи каркасов при режиме outside / both (по умолчанию «вне»)"),
      code_field: P("string", "имя поля кода (по умолчанию CODE)"),
      frac_field: P("string", "имя поля доли объёма (при дроблении по умолчанию FRAC; по центру — поле не пишется, пока имя не задано)"),
      name: P("string", "имя новой модели при дроблении"),
      expected_version: P("integer", "версия модели, которую вы читали (по умолчанию — текущая в дереве)"),
    },
    confirm: true,
  },
  {
    id: "blocks.regularize",
    title: "Регуляризация",
    description:
      "Регуляризация модели (8.7.5): новая регулярная модель в тех же осях с другим размером блока size; объём, тоннаж и металл сохраняются (точные перекрытия). Содержания (grades) усредняются по объёму или тоннажу " +
      "(AU:volume; CU:tonnes — вес по тоннажу требует плотность density: поле или число т/м³), без grades — все числовые поля по объёму. Категории — мода по объёму и поля долей P_<КОД> (categories; «нет» — не переносить). " +
      "Пропуск в данных не превращается в ноль, поэтому итоги до и после могут расходиться. Без аргументов открывает окно. Ход: «Чтение», «Перекрытия», «Запись».",
    params: {
      model_id: P("integer", "номер блочной модели (с блоками, регулярный шаг)"),
      size: P("string", "размер нового блока X; Y; Z в метрах (одно число — для всех осей), например 20; 20; 10"),
      grades: P("string", "поля через «;» с весом: AU:volume; CU:tonnes (по умолчанию — все числовые поля по объёму)"),
      density: P("string", "плотность: имя поля или число т/м³ (нужна для веса tonnes и тоннажа итогов)"),
      categories: P("string", "категорийные поля через «;» (по умолчанию — все; «нет» — не переносить)"),
      name: P("string", "имя новой модели (по умолчанию «‹имя› (регуляризация)»)"),
      folder: P("string", "папка проекта для модели"),
    },
    confirm: true,
  },
  {
    id: "blocks.rebuild",
    title: "Перестроить модель",
    description:
      "Перестраивает модель, созданную заполнением, кодированием с дроблением или регуляризацией, заново по тем же каркасам и настройкам (когда они изменились — модель «устарела»). Модель переписывается на месте (та же модель, версия + 1); " +
      "поля других операций (калькулятор, плотность, категории) считались по старым строкам и удаляются — окно спрашивает об этом до запуска, итог называет удалённые поля. Источник удалён — 409 source_lost.",
    params: {
      model_id: P("integer", "номер блочной модели (по умолчанию — выбранная в дереве)"),
      expected_version: P("integer", "версия модели, которую вы читали (по умолчанию — текущая)"),
    },
    confirm: true,
  },
];

/** The ribbon: the tab «Блочные модели» (before it exists — «Настройки проекта»), the groups «Создание» and «Поля». */
export interface BuildButton {
  command: string;
  args?: Record<string, unknown>;
  label: string;
  icon: IconName;
  hint: string;
  small?: boolean;
}

export const CREATE_GROUP_TITLE = "Создание";
export const FIELD_GROUP_TITLE = "Поля";

export const CREATE_BUTTONS: readonly BuildButton[] = [
  { command: "blocks.prototype", label: "Прототип", icon: "blocks" as IconName, hint: "Сетка блоков по охвату объектов: размер блока, поворот вокруг Z, субблоки, предпросмотр рамки в 3D" },
  { command: "blocks.fill", label: "Заполнить каркасами", icon: "blocks" as IconName, hint: "Новая модель из прототипа: код тела и доля объёма по замкнутым каркасам, режимы внутри / снаружи, приоритеты" },
  { command: "blocks.code", label: "Кодировать каркасами", icon: "blocks" as IconName, hint: "Код тела в готовой модели: по центру блока или с дроблением граничных блоков" },
];

export const FIELD_BUTTONS: readonly BuildButton[] = [
  { command: "blocks.regularize", label: "Регуляризация", icon: "blocks" as IconName, hint: "Новая сетка с другим размером блока: объём, тоннаж и металл сохраняются, категории — мода и доли" },
];
