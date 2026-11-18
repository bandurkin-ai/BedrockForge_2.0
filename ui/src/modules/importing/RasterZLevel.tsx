/**
 * «Уровень растров по Z» of the wizard «Импорт файлов» (ТЗ 7.19; ADR 0011 §5): «высота, м» — instead of the
 * height of the georeference, «сдвиг, м» — ± for every raster, the corners of a vertical one too. Numbers are
 * typed with a comma or a point; an empty field is «not set», never 0. Below — where each raster will stand.
 */
import { parseLevel, rasterLevel, type FileOk, type Level } from "./objects";
import css from "./ImportWizard.module.css";

export function RasterZLevel({ level, files, onChange }: { level: Level; files: FileOk[]; onChange: (patch: Partial<Level>) => void }) {
  const parsed = parseLevel(level);
  const rasters = files.flatMap((f) => f.rasters.map((r) => ({ file: f.file, raster: r })));
  const field = (key: keyof Level, label: string, placeholder: string, title: string) => (
    <label title={title}>
      {label}
      <input
        value={level[key]}
        placeholder={placeholder}
        inputMode="decimal"
        aria-label={label}
        aria-invalid={parsed.errors[key] ? true : undefined}
        onChange={(e) => onChange({ [key]: e.target.value })}
      />
      {parsed.errors[key] && <span className={css.error}>{parsed.errors[key]}</span>}
    </label>
  );
  return (
    <section className={css.card} aria-label="Уровень растров по Z" data-testid="raster-z-level">
      <h4>Уровень растров по Z</h4>
      <div className={css.levelRow}>
        {field("elevation", "Высота, м", "из привязки", "Вместо высоты из файла привязки — для растров в плане. Пусто — высота привязки; без неё растр ляжет под самый низкий объект.")}
        {field("shift", "Сдвиг, м", "0", "Плюс — вверх, минус — вниз: для всех растров, у вертикальных сдвигаются все углы.")}
      </div>
      <ul className={css.summary}>
        {rasters.map(({ file, raster }) => {
          const place = rasterLevel(raster, parsed);
          return (
            <li key={file} className={place.warn ? css.warn : ""}>
              {file}: {place.text}
            </li>
          );
        })}
      </ul>
      <div className={css.note}>Числа — с запятой или точкой: 250,5 или 250.5. Высоту и сдвиг потом можно изменить в свойствах растра.</div>
    </section>
  );
}
