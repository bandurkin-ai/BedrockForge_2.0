/**
 * The «ЦТМ» section of the data tree (ТЗ 7.20, 7.35 Д-07; ADR 0011 §5): wireframes with the settings of a
 * relief. A row has no gear: a click on it selects the DTM and opens its settings at the top of «Свойства»
 * (the same as «Настройки ЦТМ…» of the menu); «Вернуть в каркасы» resets the settings.
 */
import { dtms } from "../wireframes/state";
import { WireframeRows } from "../wireframes/WireframesTree";

export function DtmTree() {
  return <WireframeRows pick={dtms} label="Цифровые топографические модели" />;
}
