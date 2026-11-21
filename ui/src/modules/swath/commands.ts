/**
 * Swath plot (ТЗ 7.30, 7.35 Д-01; ADR 0019 §5–6, §9): a floating window per swath, the box in the main 3D with
 * handles of three angles. A swath is an object of the project (section «Аудит и статистика»).
 */
import type { Command } from "../../shell/commands";

export const commands: Command[] = [
  {
    id: "stats.swath",
    title: "SWATH PLOT",
    description:
      "Swath plot — средние по полосам вдоль оси короба по блочным моделям и пробам (до 12 рядов). Без swath_id — создать swath по умолчанию (первая модель и активный набор) в «Аудит и статистика» и открыть окно; с swath_id — поменять его настройки (направление x / y / z или angles — азимут 0–360° от севера, наклон −90…+90° (отрицательный — ось вниз), поворот −180…+180°; ширина полос, привязка границы, диапазон показа) и открыть окно. Ответ — итоги рядов.",
    params: {
      swath_id: { type: "integer", description: "номер swath (без — новый)" },
      direction: { type: "string", enum: ["x", "y", "z", "angles"], description: "ось полос: x, y, z или по углам" },
      azimuth: { type: "number", description: "азимут оси полос, ° (0–360 от севера по часовой)" },
      dip: { type: "number", description: "наклон оси полос, ° (−90…+90, отрицательный — вниз)" },
      roll: { type: "number", description: "поворот короба вокруг оси полос, ° (−180…+180)" },
      width: { type: "number", description: "ширина полосы, м (не больше 2000 полос)" },
      anchor: { type: "number", description: "координата вдоль оси полос, через которую проходит граница полос" },
      range_from: { type: "integer", description: "первая показанная полоса (номер с 1)" },
      range_to: { type: "integer", description: "последняя показанная полоса" },
    },
    stage: "Э13",
  },
  {
    id: "stats.swath_3d",
    title: "Полосы swath в 3D",
    description: "Показать или скрыть короб и полосы swath в главном 3D-окне вместе с графиком над ним.",
    params: { swath_id: { type: "integer", description: "номер swath" }, visible: { type: "boolean", description: "true — показать, false — скрыть" } },
    required: ["swath_id", "visible"],
    stage: "Э13",
  },
];
