/** Geometry of the displays along holes (ported from BF downholeGeometry.ts, second half). */
import type { DownholeData, TrajectoryHole } from "./types";
import { type MarkerShape, type StripLogSettings, type Track } from "../../charts/striplog/model";
import { parseNumber } from "../util/numbers";
import { type DownholeBuild, EVENT_LABEL_M, FRAME_COLOR, type LabelCandidate, MAX_LABELS, type MarkerLayer, NO_DATA_COLOR, RADIUS_STEPS, type Rgb, type SideMesh, type SidePoints, type Span, type StackStart, type TubeLayer, type Vec, autoPanelWidth, cleanValue, clip, emptyMesh, hexRgb, holeKey, labelText, passesFilter, pathBetween, placeColumns, pointAt, positionOf, resolve, tubeTracks, unit, valueText } from "./geometry";

export function buildDownhole(
  trajectories: TrajectoryHole[],
  data: DownholeData,
  settings: StripLogSettings,
  origin: [number, number, number],
  start: StackStart = { units: 0, tubes: 0 },
): DownholeBuild {
  // Hole names are matched without regard to case and outer spaces (tables often differ in that).
  const holes = new Map(trajectories.map((h) => [holeKey(h.hole_id), h]));
  const tracks = settings.tracks.filter((t) => t.visible !== false);
  const resolved = tracks.map((t) => resolve(data, t));
  const tubeIds = tubeTracks(tracks);
  const panelWidth = settings.panel && settings.panel > 0 ? settings.panel : autoPanelWidth(trajectories);
  const from = parseNumber(settings.from);
  const to = parseNumber(settings.to);
  const tubeWidth = Math.max(1, settings.tube ?? 8);
  const metres = (s: Span) => panelWidth * s.units + s.m;

  const build: DownholeBuild = {
    tubes: [], panels: emptyMesh(), lines: emptyMesh(), points: [], labels: [], markers: [], legend: [], panelWidth, holes: 0, intervals: 0,
  };

  // Panel columns, side by side from the hole outwards on each side: offset and width in metres.
  const columns = new Map<string, { offset: number; width: number; sides: number[] }>();
  for (const [id, c] of placeColumns(tracks, start).columns) {
    columns.set(id, { offset: panelWidth * 0.15 + metres(c.offset), width: metres(c.width), sides: c.sides });
  }

  const rel = (p: Vec): Vec => [p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]];
  let tubeIndex = start.tubes;
  // Tubes of a layer: one, or several thicknesses when the value sets it.
  const tubesOf = new Map<string, TubeLayer[]>();
  const markersOf = new Map<string, Map<MarkerShape, MarkerLayer>>();
  const pointsOf = new Map<string, SidePoints>();
  const candidates = new Map<string, LabelCandidate[]>();
  for (const r of resolved) {
    const t = r.track;
    const opacity = Math.max(0.05, Math.min(1, t.opacity ?? 1));
    if (tubeIds.has(t.id)) {
      const index = tubeIndex++;
      const fixed = t.radius?.by === "fixed" ? t.radius.max : Math.max(2, tubeWidth - 2.5 * index);
      const widths = t.radius?.by === "value" && r.numeric
        ? Array.from({ length: RADIUS_STEPS }, (_, i) => t.radius!.min + ((t.radius!.max - t.radius!.min) * i) / (RADIUS_STEPS - 1))
        : [fixed];
      const layers = widths.map((w) => ({ trackId: t.id, width: Math.max(1, w), opacity, positions: [], colors: [] }) as TubeLayer);
      tubesOf.set(t.id, layers);
      build.tubes.push(...layers);
    }
    if (t.type === "event") markersOf.set(t.id, new Map());
    if (t.type === "points" || (t.type === "line" && t.dots)) {
      const layer: SidePoints = { trackId: t.id, size: Math.max(2, t.pointSize ?? (t.type === "points" ? 7 : 5)), opacity, positions: [], tangents: [], amps: [], colors: [] };
      pointsOf.set(t.id, layer);
      build.points.push(layer);
    }
    if (t.label?.on) candidates.set(t.id, []);
  }
  const markerLayer = (t: Track, shape: MarkerShape): MarkerLayer => {
    const byShape = markersOf.get(t.id) as Map<MarkerShape, MarkerLayer>;
    let layer = byShape.get(shape);
    if (!layer) {
      layer = { trackId: t.id, shape, size: Math.max(4, t.size ?? 12), opacity: Math.max(0.05, Math.min(1, t.opacity ?? 1)), positions: [], colors: [] };
      byShape.set(shape, layer);
      build.markers.push(layer);
    }
    return layer;
  };

  /** A quad from the hole outwards: along the interval, from `a0` to `a1` metres to the side. */
  const addTriangles = (m: SideMesh, a: Vec, b: Vec, t: Vec, a0: number, a1: number, c: Rgb, alpha: number) => {
    const base = m.amps.length;
    const verts: [Vec, number][] = [[a, a0], [b, a0], [b, a1], [a, a1]];
    for (const [p, amp] of verts) {
      m.positions.push(p[0], p[1], p[2]);
      m.tangents.push(t[0], t[1], t[2]);
      m.amps.push(amp);
      m.colors.push(c[0], c[1], c[2], alpha);
    }
    m.index.push(base, base + 1, base + 2, base, base + 2, base + 3);
  };
  const addSegment = (m: SideMesh, a: Vec, b: Vec, t: Vec, a0: number, a1: number, c: Rgb, alpha = 1) => {
    for (const [p, amp] of [[a, a0], [b, a1]] as [Vec, number][]) {
      m.positions.push(p[0], p[1], p[2]);
      m.tangents.push(t[0], t[1], t[2]);
      m.amps.push(amp);
      m.colors.push(c[0], c[1], c[2], alpha);
    }
  };

  const frame = hexRgb(FRAME_COLOR);
  for (const hole of data.holes) {
    const traj = holes.get(holeKey(hole.hole_id));
    if (!traj || traj.path.length < 1 || traj.path[0].length < 4) continue;
    build.holes += 1;
    // Depths where each panel column drew a value: its base line runs only there.
    const drawn = new Map<string, [number, number][]>();
    for (const r of resolved) {
      const t = r.track;
      const rows = (hole.tables[t.table] ?? []).filter((iv) => iv.from !== null && iv.to !== null);
      const col = columns.get(t.id);
      const tubes = tubesOf.get(t.id);
      const wantsColor = t.type === "color" || t.type === "color_text";
      const graph = t.type === "bars" || t.type === "steps" || t.type === "line" || t.type === "points";
      const legacyText = (t.type === "text" || t.type === "color_text") && !t.label && settings.labels !== false;
      const alpha = Math.max(0.05, Math.min(1, t.opacity ?? 1));
      const noData = hexRgb(t.noDataColor ?? NO_DATA_COLOR);
      const points = pointsOf.get(t.id);
      const wanted = candidates.get(t.id);
      const lo = r.range?.[0] ?? 0;
      const hi = r.range?.[1] ?? 1;
      const cap = t.over === "asis" ? Infinity : 1;
      const split = typeof t.split === "number" && r.range ? Math.min(1, positionOf(t.split, lo, hi, t.log)) : null;
      const gap = t.type === "bars" ? Math.max(0, Math.min(0.8, t.gap ?? 0)) : 0;
      // the previous interval of a graph on each side: where its line ended
      const prev = new Map<number, { depth: number; amp: number; point: Vec }>();
      for (const iv of rows) {
        const span = clip(iv, from, to, t.type === "event");
        if (!span) continue;
        const raw = iv.values[t.field];
        const value = r.numeric ? cleanValue(t, raw) : null;
        if (r.numeric && !passesFilter(t, value)) {
          prev.clear();
          continue;
        }
        let color = r.color(iv, value);
        if (color === "hidden") {
          prev.clear();
          continue;
        }
        if (color === null && (t.filter?.empty === "nodata" || (r.numeric && value !== null && t.colorBy === "classes"))) color = noData;
        build.intervals += 1;
        const item = r.itemOf(iv, value);
        if (item >= 0) r.counts[item] += 1;
        const markers = markersOf.get(t.id);
        if (markers) {
          const p = rel(pointAt(traj.path, span[0]));
          const c = color ?? hexRgb(t.color);
          const key = raw === null || raw === undefined ? "" : String(raw);
          const layer = markerLayer(t, t.catMarkers?.[key] ?? t.marker ?? "diamond");
          layer.positions.push(p[0], p[1], p[2]);
          layer.colors.push(c[0], c[1], c[2]);
          const text = t.label?.on ? labelText(t.label.template, iv, t.label.decimals) : valueText(raw);
          const show = t.label ? t.label.on : settings.labels !== false;
          if (show && text && build.labels.length < MAX_LABELS) {
            build.labels.push({
              position: p, tangent: [0, 0, -1], amp: 0, text, length: EVENT_LABEL_M, trackId: t.id, px: layer.size / 2 + 4,
              ...(t.label?.on ? { size: t.label.size, color: t.label.color === "class" ? undefined : t.label.color } : {}),
            });
          }
          continue;
        }
        const pa = rel(pointAt(traj.path, span[0]));
        const pb = rel(pointAt(traj.path, span[1]));
        const tan = unit(pa, pb);
        if (wantsColor && color) {
          if (tubes) {
            const layer = tubes.length > 1 && value !== null && r.range
              ? tubes[Math.round(Math.min(1, positionOf(value, lo, hi, t.radius?.log ?? t.log)) * (tubes.length - 1))]
              : tubes[0];
            const pts = pathBetween(traj.path, span[0], span[1]).map(rel);
            for (let i = 0; i + 1 < pts.length; i++) {
              layer.positions.push(...pts[i], ...pts[i + 1]);
              layer.colors.push(...color, ...color);
            }
          } else if (col) {
            for (const s of col.sides) addTriangles(build.panels, pa, pb, tan, s * col.offset, s * (col.offset + col.width), color, alpha);
            if (!drawn.has(t.id)) drawn.set(t.id, []);
            drawn.get(t.id)!.push(span);
          }
        }
        // the far end of the interval's mark beside the hole: where its label goes
        let reach = col ? col.sides[0] * (col.offset + (wantsColor && !tubes ? col.width : 0)) : panelWidth * 0.15;
        if (graph && r.numeric && col && value !== null && r.range) {
          const p = Math.min(cap, positionOf(value, lo, hi, t.log));
          const c = color ?? hexRgb(FRAME_COLOR);
          // Sides the value is drawn on, and how far: a split sends values below the threshold to the left.
          if (!drawn.has(t.id)) drawn.set(t.id, []);
          drawn.get(t.id)!.push(span);
          const marks: [number, number][] = split === null
            ? col.sides.map((s) => [s, p] as [number, number])
            : [p >= split ? [1, split < 1 ? (p - split) / (1 - split) : 0] : [-1, split > 0 ? (split - p) / split : 0]];
          for (const [s, share] of marks) {
            const base = s * col.offset;
            const amp = s * (col.offset + share * col.width);
            reach = amp;
            if (t.type === "bars") {
              let a = pa;
              let b = pb;
              if (gap > 0) {
                const cut = ((span[1] - span[0]) * gap) / 2;
                a = rel(pointAt(traj.path, span[0] + cut));
                b = rel(pointAt(traj.path, span[1] - cut));
              }
              addTriangles(build.panels, a, b, tan, base, share > 0.01 ? amp : s * (col.offset + col.width * 0.01), c, alpha);
            } else if (t.type === "steps") {
              if (t.fill !== false) addTriangles(build.panels, pa, pb, tan, base, amp, c, alpha * 0.55);
              addSegment(build.lines, pa, pb, tan, amp, amp, c);
              const before = prev.get(s);
              addSegment(build.lines, pa, pa, tan, before && Math.abs(before.depth - span[0]) < 1e-6 ? before.amp : base, amp, c);
              prev.set(s, { depth: span[1], amp, point: pb });
            } else {
              const mid = rel(pointAt(traj.path, (span[0] + span[1]) / 2));
              if (t.type === "line") {
                const before = prev.get(s);
                if (before && Math.abs(before.depth - span[0]) < 1e-6) addSegment(build.lines, before.point, mid, tan, before.amp, amp, c);
                prev.set(s, { depth: span[1], amp, point: mid });
              }
              if (points) {
                points.positions.push(mid[0], mid[1], mid[2]);
                points.tangents.push(tan[0], tan[1], tan[2]);
                points.amps.push(amp);
                points.colors.push(c[0], c[1], c[2]);
              }
            }
            if (t.over === "mark" && value > hi) {
              const tick = s * (col.offset + col.width * 1.06);
              addSegment(build.lines, pa, pb, tan, tick, tick, hexRgb("#e0364a"));
            }
          }
        } else if (graph) {
          // a gap in the data: steps close down to the hole, a line breaks
          if (t.type === "steps" && col) for (const [s, before] of prev) addSegment(build.lines, pa, pa, tan, before.amp, s * col.offset, hexRgb(t.color));
          prev.clear();
        }
        if (t.boundaries && col && !tubes) for (const s of col.sides) addSegment(build.lines, pa, pa, tan, s * col.offset, s * (col.offset + col.width), frame);
        if (legacyText && build.labels.length < MAX_LABELS) {
          const text = valueText(raw);
          if (text) {
            const mid = rel(pointAt(traj.path, (span[0] + span[1]) / 2));
            const s = col ? col.sides[0] : 1;
            build.labels.push({ position: mid, tangent: tan, amp: s * (col ? col.offset : panelWidth * 0.15), text, length: span[1] - span[0], trackId: t.id, ...(s < 0 ? { right: true } : {}) });
          }
        }
        if (wanted && t.label && (typeof t.label.min !== "number" || (value !== null && value >= t.label.min))) {
          const text = labelText(t.label.template, iv, t.label.decimals);
          if (text) {
            const mid = rel(pointAt(traj.path, (span[0] + span[1]) / 2));
            const side = reach < 0 ? -1 : 1;
            const amp = t.label.place === "trace" ? side * panelWidth * 0.05 : reach + side * panelWidth * 0.04;
            const hex = t.label.color === "class" ? (color ? rgbHex(color) : undefined) : t.label.color;
            wanted.push({
              value: value ?? 0,
              label: { position: mid, tangent: tan, amp, text, length: span[1] - span[0], trackId: t.id, size: t.label.size, color: hex, ...(side < 0 ? { right: true } : {}) },
            });
          }
        }
      }
      // steps end at the hole
      if (t.type === "steps" && col) {
        for (const [s, before] of prev) addSegment(build.lines, before.point, before.point, [0, 0, -1], before.amp, s * col.offset, hexRgb(t.color));
      }
    }
    // The base line of every panel column (its outer edge is not drawn: a line over the graph only hides it).
    // It follows the hole through its survey stations and runs only where the column has values: a straight
    // line from the collar to the deepest interval would stand apart from a bent hole and go on below the data.
    for (const r of resolved) {
      const col = columns.get(r.track.id);
      const spans = drawn.get(r.track.id);
      if (!col || !spans?.length || tubeIds.has(r.track.id) || r.track.type === "text" || r.track.type === "event") continue;
      const sides = typeof r.track.split === "number" ? [1, -1] : col.sides;
      for (const [a, b] of mergeSpans(spans)) {
        const pts = pathBetween(traj.path, a, b).map(rel);
        for (let i = 0; i + 1 < pts.length; i++) {
          const tan = unit(pts[i], pts[i + 1]);
          for (const s of sides) addSegment(build.lines, pts[i], pts[i + 1], tan, s * col.offset, s * col.offset, frame);
        }
      }
    }
  }

  // Labels by a template: when there are too many, the highest values stay.
  let room = MAX_LABELS - build.labels.length;
  for (const r of resolved) {
    const list = candidates.get(r.track.id);
    if (!list?.length) continue;
    const limit = Math.max(0, Math.min(room, r.track.label?.limit && r.track.label.limit > 0 ? r.track.label.limit : room));
    if (list.length > limit) list.sort((a, b) => b.value - a.value);
    for (const c of list.slice(0, limit)) build.labels.push(c.label);
    room -= Math.min(list.length, limit);
  }

  for (const r of resolved) {
    if (!(r.colors || r.numeric || r.track.type === "text")) continue;
    build.legend.push({
      trackId: r.track.id, title: r.track.field, items: r.items, counts: r.counts, range: r.numeric ? r.range : null, unit: r.unit,
      ...(r.gradient ? { gradient: r.gradient } : {}),
    });
  }
  build.tubes = build.tubes.filter((t) => t.positions.length > 0);
  return build;
}

/** Depth spans joined where they touch or overlap, from the top down. */
export function mergeSpans(spans: [number, number][]): [number, number][] {
  const sorted = [...spans].sort((a, b) => a[0] - b[0]);
  const out: [number, number][] = [];
  for (const [a, b] of sorted) {
    const last = out[out.length - 1];
    if (last && a <= last[1] + 1e-6) last[1] = Math.max(last[1], b);
    else out.push([a, b]);
  }
  return out;
}

function rgbHex(c: Rgb): string {
  return `#${c.map((v) => Math.round(v * 255).toString(16).padStart(2, "0")).join("")}`;
}

