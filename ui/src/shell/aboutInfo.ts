/**
 * «О производителе» (ТЗ 7.35 Д-12, ADR 0008; answer № 5 of the owner): the texts of the plate. Contacts and
 * links are not shown yet — adding them is a change of this file (and of 17.4.1 if they are links).
 */
import { Store } from "./store";

export const ABOUT = {
  product: "BedrockForge",
  byline: "by Mikhail Bandurkin",
  slogan: "Геологическое моделирование · Планирование горных работ · Разведка",
  maker: "Производитель: Bandurkin Mikhail",
} as const;

/** Lines of the plate after the brand: the maker and the versions («—» while unknown). */
export function aboutLines(appVersion: string | null | undefined, coreVersion: string | null | undefined): string[] {
  return [ABOUT.maker, `Версия программы: ${appVersion || "—"}`, `Версия ядра: ${coreVersion || "—"}`];
}

/** The text «Скопировать» puts on the clipboard. */
export function aboutText(appVersion: string | null | undefined, coreVersion: string | null | undefined): string {
  return [`${ABOUT.product} ${ABOUT.byline}`, ABOUT.slogan, ...aboutLines(appVersion, coreVersion)].join("\n");
}

/** Open state of the plate (a click on ⓘ, `app.about`, Esc, a click elsewhere). */
export const aboutStore = new Store<{ open: boolean }>({ open: false });

export function toggleAbout(open?: boolean): boolean {
  const next = open ?? !aboutStore.get().open;
  aboutStore.set({ open: next });
  return next;
}
