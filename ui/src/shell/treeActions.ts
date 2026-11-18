/**
 * What a row of an object tree offers (ТЗ 7.1, 7.0.13): its context menu and its buttons as data, so the
 * sections «Линии», «Растры», «Каркасы» and «ЦТМ» are built — and tested — without React. An action is a
 * registry command with its arguments (rename, delete, export… ask in their own windows), or the local one of
 * the tree: «Свойства» — select the object and open the panel.
 */
import type { IconName } from "./Icon";

export interface Confirm {
  title: string;
  message: string;
  okLabel: string;
  danger?: boolean;
}

export interface TreeAction {
  label: string;
  /** the sign of the action when it is a button of the row */
  icon?: IconName;
  /** registry command (the menu item is disabled with the command's reason while it is unavailable) */
  command?: string;
  args?: Record<string, unknown>;
  /** the action of the tree itself: select the object and open «Свойства» */
  local?: "properties";
  /** asked in an own dialog before the command runs */
  confirm?: Confirm;
  separator?: boolean;
}

export const SEPARATOR: TreeAction = { label: "", separator: true };

/** A row of an object: what a click on its name does, the buttons at its right and its context menu. */
export interface ObjectRow {
  testId: string;
  title: string;
  /** "select" — `object.select`; "properties" — select and open «Свойства» */
  click: "select" | "properties";
  buttons: TreeAction[];
  menu: TreeAction[];
}

// one formatter for all rows: `toLocaleString` makes a new one on every call (a file may have thousands of layers)
const INTEGER = new Intl.NumberFormat("ru-RU");

/** «1 234» — thousands by a space, as everywhere in the interface. */
export const count = (n: number): string => INTEGER.format(n);

/** Russian plural: plural(21, "слой", "слоя", "слоёв") → «слой». */
export function plural(n: number, one: string, few: string, many: string): string {
  const d = Math.abs(n) % 100;
  const u = d % 10;
  if (d > 10 && d < 20) return many;
  if (u === 1) return one;
  return u >= 2 && u <= 4 ? few : many;
}

/** «12 линий» */
export const counted = (n: number, one: string, few: string, many: string): string => `${count(n)} ${plural(n, one, few, many)}`;
