import { useState } from "react";
import type { ColorClass, Track } from "../../charts/stripLog";
import { gradientCss } from "./gradients";
import type { PaletteBody, StyleRec, TemplateBody } from "./styles";

function layerColors(body: Partial<Track>): string[] {
  if (body.colorBy === "classes" && body.classes?.length) return body.classes.map((c: ColorClass) => c.color);
  if (body.catColors && Object.keys(body.catColors).length) return Object.values(body.catColors).slice(0, 5);
  return [body.color ?? "#4f9dde"];
}

/** A small picture of a style: its colours in the shape of its mark. */
export function StyleSwatch({ style }: { style: Pick<StyleRec, "level" | "body"> }) {
  if (style.level === "palette") {
    const b = style.body as PaletteBody;
    if (b.kind === "gradient") return <i className="dh-sample" style={{ background: gradientCss(b.gradient.name, b.gradient.reverse) }} />;
    const colors = b.kind === "classes" ? b.classes.map((c) => c.color) : Object.values(b.catColors ?? {}).slice(0, 6);
    return <i className="dh-sample">{(colors.length ? colors : ["#9aa3b5"]).map((c, i) => <b key={i} style={{ background: c }} />)}</i>;
  }
  const layers = style.level === "display" ? ((style.body as unknown as TemplateBody).tracks ?? []).slice(0, 4) : [style.body as Partial<Track>];
  return (
    <svg className="dh-sample" viewBox="0 0 36 16" aria-hidden>
      {layers.map((b, k) => {
        const w = 36 / layers.length;
        const x0 = k * w;
        const colors = layerColors(b);
        const col = (i: number) => (b.colorBy === "scale" ? ["#2b5fa8", "#8fd15a", "#ee7a24", "#b2182b"][i % 4] : colors[i % colors.length]);
        if (b.type === "line") return <polyline key={k} fill="none" stroke={col(0)} strokeWidth={1.6} points={[0.1, 0.5, 0.25, 0.8, 0.45].map((v, i) => `${x0 + 1 + v * (w - 3)},${2 + i * 3}`).join(" ")} />;
        if (b.type === "points") return <g key={k}>{[0.3, 0.7, 0.4, 0.9].map((v, i) => <circle key={i} cx={x0 + 1 + v * (w - 3)} cy={2.5 + i * 3.6} r={1.5} fill={col(i)} />)}</g>;
        if (b.type === "event") return <path key={k} d={`M${x0 + w / 2} 2 l5 6 l-5 6 l-5 -6 z`} fill={col(0)} />;
        if (b.type === "text") return <text key={k} x={x0 + w / 2} y={12} textAnchor="middle" fontSize={11} fill="currentColor">Т</text>;
        if (b.type === "color" || b.type === "color_text") return <g key={k}>{[0, 1, 2, 3].map((i) => <rect key={i} x={x0 + w / 2 - (b.form === "panel" ? 4 : 2)} y={i * 4} width={b.form === "panel" ? 8 : 4} height={4} fill={col(i)} />)}</g>;
        return <g key={k}>{[0.4, 0.9, 0.6, 0.25].map((v, i) => <rect key={i} x={x0 + 1} y={i * 4} width={v * (w - 3)} height={b.type === "steps" ? 4 : 3} fill={col(i)} opacity={b.type === "steps" ? 0.7 : 1} />)}</g>;
      })}
    </svg>
  );
}

/** The list of styles to take: a search, the styles of the project, then the built-in ones. */
export function StyleGallery({ styles, currentId, onPick }: { styles: StyleRec[]; currentId?: number; onPick: (s: StyleRec) => void }) {
  const [query, setQuery] = useState("");
  const q = query.trim().toUpperCase();
  const found = styles.filter((s) => !q || s.name.toUpperCase().includes(q));
  const groups: [string, StyleRec[]][] = [["Проект", found.filter((s) => !s.builtin)], ["Встроенные", found.filter((s) => s.builtin)]];
  return (
    <div className="dh-gallery">
      <input value={query} placeholder="поиск стиля" aria-label="Поиск стиля" autoFocus onChange={(e) => setQuery(e.target.value)} />
      {groups.map(([title, list]) => list.length > 0 && (
        <div key={title} className="dh-gallery-group">
          <span className="muted small">{title}</span>
          {list.map((s) => (
            <button key={s.id} type="button" role="menuitem" className={`dh-gallery-item${s.id === currentId ? " active" : ""}`} title={`Применить «${s.name}» (копией)${s.default_for.length ? `; по умолчанию для: ${s.default_for.join(", ")}` : ""}`} onClick={() => onPick(s)}>
              <StyleSwatch style={s} />
              <span className="grow">{s.name}</span>
              {s.default_for.length > 0 && <span className="muted small" title={`По умолчанию для полей: ${s.default_for.join(", ")}`}>★</span>}
            </button>
          ))}
        </div>
      ))}
      {!found.length && <span className="muted small">{styles.length ? "Ничего не найдено." : "В библиотеке пока нет стилей: настройте и выберите «Сохранить как…»."}</span>}
    </div>
  );
}
