import { useEffect, useState } from "react";
import { api, CoreError } from "../../shell/api";
import { useStore } from "../../shell/useStore";
import { exampleText, SEVERITY_TITLES, type Finding } from "../audit/state";
import { cellText, tablesStore } from "./state";
import css from "./Tables.module.css";

interface Card {
  id: number;
  name: string;
  type: { key: string; title: string };
  collar: Record<string, unknown> | null;
  surveys: number;
  tables: Record<string, { name: string; kind: string; rows: number }>;
  trajectory: { length: number | null; end: number[] | null; skipped: boolean };
}

const LABELS: Record<string, string> = { x: "X", y: "Y", z: "Z", td: "Глубина", azimuth: "Азимут", dip: "Наклон" };

/** «Карточка скважины» in «Свойства» (ТЗ 7.10): the collar, rows per table, the trajectory; audit findings — Э5. */
export function HoleCard() {
  const s = useStore(tablesStore, (x) => x);
  const [card, setCard] = useState<Card | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setCard(null);
    setError(null);
    if (!s.hole || s.setId === null) return;
    api.get<Card>(`/drillholes/sets/${s.setId}/holes/${s.hole.id}`).then(setCard, (e: unknown) => setError(e instanceof CoreError ? e.full : String(e)));
  }, [s.hole, s.setId, s.version]);
  if (!s.hole) return <div className={css.empty}>Выберите скважину в таблице (щелчок по номеру) — здесь появится её карточка.</div>;
  if (error) return <div className={css.error}>{error}</div>;
  if (!card) return <div className={css.note}>Загрузка…</div>;
  return (
    <div className={css.card} aria-label={`Скважина ${card.name}`}>
      <h4>
        {card.type.title} {card.name}
      </h4>
      {card.collar ? (
        <dl>
          {Object.entries(card.collar).map(([k, v]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt>{LABELS[k] ?? k}</dt>
              <dd>{cellText(v, typeof v === "number" ? "number" : "text") || "—"}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <div className={css.error}>Нет устья: траектория не строится.</div>
      )}
      <dl>
        <dt>Замеров инклинометрии</dt>
        <dd>{card.surveys}</dd>
        {Object.entries(card.tables).map(([id, t]) => (
          <div key={id} style={{ display: "contents" }}>
            <dt>{t.name}</dt>
            <dd>{t.rows.toLocaleString("ru-RU")} строк</dd>
          </div>
        ))}
        <dt>Длина ствола</dt>
        <dd>{card.trajectory.length === null ? "—" : `${cellText(card.trajectory.length, "number")} м`}</dd>
        <dt>Забой (X, Y, Z)</dt>
        <dd>{card.trajectory.end ? card.trajectory.end.map((v) => cellText(Math.round(v * 100) / 100, "number")).join("; ") : "—"}</dd>
      </dl>
      <HoleFindings setId={s.setId} hole={card.name} version={s.version} />
    </div>
  );
}

/** Findings of the last audit for this hole (ТЗ 7.10 «Карточка скважины: устье и находки аудита»). */
function HoleFindings({ setId, hole, version }: { setId: number | null; hole: string; version: number }) {
  const [list, setList] = useState<Finding[] | null>(null);
  useEffect(() => {
    if (setId === null) return;
    api.get<{ findings: Finding[] }>(`/audit/sets/${setId}/findings?hole=${encodeURIComponent(hole)}&limit=100`).then((r) => setList(r.findings), () => setList([]));
  }, [setId, hole, version]);
  if (list === null) return null;
  if (!list.length) return <div className={css.note}>Аудит: замечаний нет (или аудит не запускался).</div>;
  return (
    <div>
      <b>Аудит</b>
      <ul>
        {list.map((f, i) => (
          <li key={i} title={f.examples.map(exampleText).join("\n")}>
            {SEVERITY_TITLES[f.severity]}: {f.code} — строк {f.count}
          </li>
        ))}
      </ul>
    </div>
  );
}
