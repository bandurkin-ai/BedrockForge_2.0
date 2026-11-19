/** Line icons on a 24px grid (ported from BF); they take the text colour. */
const PATHS = {
  cube: "M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zM4 7.5l8 4.5 8-4.5M12 12v9",
  cubeTop: "M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zM4 7.5l8 4.5 8-4.5M12 12v9",
  cubeLeft: "M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zM4 7.5l8 4.5 8-4.5M12 12v9",
  cubeRight: "M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zM4 7.5l8 4.5 8-4.5M12 12v9",
  plan: "M4 6l5-2 6 2 5-2v14l-5 2-6-2-5 2V6zM9 4v14M15 6v14",
  section: "M3 18h18M3 18V8l5 3 4-5 4 6 5-3v9M8 11v7M16 12v6",
  log: "M9 3h6v18H9zM9 8h6M9 12h6M9 17h6M5 3v18",
  logFile: "M6 3h9l3 3v15H6zM15 3v3h3M9 10h6M9 13.5h6M9 17h4",
  copy: "M9 9h11v11H9zM5 15H4V4h11v1",
  striplog: "M5 3v18M9 3h4v18H9zM9 8h4M9 13h4M16 5h2M16 9h4M16 13h1M16 17h3",
  table: "M3 5h18v14H3zM3 10h18M3 14.5h18M9 5v14",
  gear: "M10.4 5.3L10.8 2.9L13.2 2.9L13.6 5.3L15.6 6.1L17.6 4.7L19.3 6.4L17.9 8.4L18.7 10.4L21.1 10.8L21.1 13.2L18.7 13.6L17.9 15.6L19.3 17.6L17.6 19.3L15.6 17.9L13.6 18.7L13.2 21.1L10.8 21.1L10.4 18.7L8.4 17.9L6.4 19.3L4.7 17.6L6.1 15.6L5.3 13.6L2.9 13.2L2.9 10.8L5.3 10.4L6.1 8.4L4.7 6.4L6.4 4.7L8.4 6.1zM12 9.0a3.0 3.0 0 100 6.0 3.0 3.0 0 000-6.0z",
  chart: "M4 4v16h16M8 15l3-4 3 2 5-6",
  histogram: "M4 20h16M6 20v-6h3v6M10.5 20V8h3v12M15 20v-9h3v9",
  swath: "M3 20h18M6 20V9M10 20V9M14 20V9M18 20V9M3 9h18M4 6l4-2 4 2.5 4-3 4 1.5",
  qq: "M4 20L20 4M4 4v16h16M7 15.5h.01M10 12.5h.01M13.5 9h.01M16.5 6.5h.01",
  dendrogram: "M4 20h16M6 20v-5h4v5M8 15v-4h7v9M11.5 11V6M17 20v-6",
  audit: "M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6l8-3zM8.5 12l2.5 2.5 4.5-5",
  import: "M12 3v12M7 10l5 5 5-5M4 19h16",
  export: "M12 15V3M7 8l5-5 5 5M4 19h16",
  plus: "M12 5v14M5 12h14",
  refresh: "M20 11a8 8 0 10-2.3 5.7M20 4v7h-7",
  search: "M11 4a7 7 0 100 14 7 7 0 000-14zM16 16l5 5",
  close: "M6 6l12 12M18 6L6 18",
  clear: "M5 5h14v14H5zM9 9l6 6M15 9l-6 6",
  single: "M4 5h16v14H4z",
  columns: "M4 5h16v14H4zM12 5v14",
  rows: "M4 5h16v14H4zM4 12h16",
  quad: "M4 5h16v14H4zM12 5v14M4 12h16",
  panelLeft: "M3 5h18v14H3zM9 5v14",
  panelRight: "M3 5h18v14H3zM15 5v14",
  panelBottom: "M3 5h18v14H3zM3 14h18",
  properties: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M19 18h1M16 4v4M10 10v4M17 16v4",
  theme: "M12 3a9 9 0 100 18 7 7 0 010-18z",
  sun: "M12 8a4 4 0 100 8 4 4 0 000-8zM12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8",
  moon: "M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z",
  info: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 11v6M12 7.5v.5",
  spark: "M12 3l1.8 5.6L19.5 10l-5.7 1.4L12 17l-1.8-5.6L4.5 10l5.7-1.4L12 3zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15z",
  chat: "M4 5h16v11H9l-5 4V5z",
  send: "M4 12l16-8-6 16-3-7-7-1z",
  maximize: "M5 9V5h4M19 9V5h-4M5 15v4h4M19 15v4h-4",
  chevronUp: "M6 15l6-6 6 6",
  chevronDown: "M6 9l6 6 6-6",
  command: "M5 8l4 4-4 4M12 17h7",
  play: "M7 4l13 8-13 8V4z",
  list: "M8 6h12M8 12h12M8 18h12M4 6h.5M4 12h.5M4 18h.5",
  hole: "M12 3v18M8 7l4-4 4 4M9 21h6",
  palette: "M4 5h6v6H4zM14 5h6v6h-6zM4 15h6v4H4zM14 15h6v4h-6z",
  stop: "M6 6h12v12H6z",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6",
  folder: "M3 6h6l2 2h10v11H3V6z",
  edit: "M4 20h4L19 9l-4-4L4 16v4zM13 7l4 4",
  chevronRight: "M9 6l6 6-6 6",
  lines: "M3 17l5-6 4 3 4-7 5 5",
  wireframe: "M12 3l8 5v8l-8 5-8-5V8l8-5zM4 8l8 5 8-5M12 13v8M8 5.5l8 5M16 5.5l-8 5",
  // Three cubes: one on two.
  blocks: "M12 4l3.9 2.25v4.5L12 13l-3.9-2.25v-4.5zM8.1 6.25L12 8.5l3.9-2.25M12 8.5V13M8.1 10.75L4.2 13v4.5l3.9 2.25L12 17.5V13M4.2 13l3.9 2.25L12 13M8.1 15.25v4.5M15.9 10.75L19.8 13v4.5l-3.9 2.25L12 17.5M12 13l3.9 2.25L19.8 13M15.9 15.25v4.5",
  // A scatter of points: a cloud of a survey.
  cloud: "M5 16.5a1 1 0 100 .1M8.5 12a1 1 0 100 .1M12 15a1 1 0 100 .1M15.5 10a1 1 0 100 .1M19 13.5a1 1 0 100 .1M10 7.5a1 1 0 100 .1M14.5 18.5a1 1 0 100 .1M7.5 19.5a1 1 0 100 .1M17.5 5.5a1 1 0 100 .1",
  raster: "M4 5h16v14H4zM4 15l4-4 4 4 3-3 5 5M15 9.5a1 1 0 100-.1",
  drillholes: "M7 3v18M17 3l-4 18M12 3v12M4 3h16",
  dtm: "M2 18l5-8 4 5 3-4 8 7zM7 10l2.5 8M14 11l1 7M4 21h16",
  section2: "M4 20L20 4M7 20L20 7M4 17L17 4",
  // ---- the string editor (after the AutoCAD drawing and modify commands)
  edSelect: "M5 3l13 8-6 1.5 3.5 7-2.5 1.2-3.5-7L5 18z",
  edPline: "M3 18l5-9 6 6 7-11M3 18h.01M8 9h.01M14 15h.01M21 4h.01",
  edPolygon: "M12 3l8.5 6.2-3.2 10H6.7L3.5 9.2z",
  edPoint: "M12 4v16M4 12h16M12 10.5a1.5 1.5 0 100 3 1.5 1.5 0 000-3z",
  edTie: "M3 7c4-3 8 3 12 0s5-1 6 0M3 18c4-3 8 3 12 0s5-1 6 0M8 8v9M15 6.5v11",
  edInsert: "M3 17l7-10 11 9M10 4v6M7 7h6",
  edUndo: "M9 14L4 9l5-5M4 9h10a6 6 0 010 12h-3",
  edRedo: "M15 14l5-5-5-5M20 9H10a6 6 0 000 12h3",
  edSave: "M5 3h11l3 3v15H5zM8 3v5h7V3M8 21v-7h8v7",
  edExit: "M14 4h5v16h-5M10 8l-4 4 4 4M6 12h10",
  edClose: "M4 18L8 5l9 1 3 9M20 15l-4 1.7M14 17.4l-4 .3M8 18H4",
  edOpen: "M9 19l-5-1 4-13 9 1 3 9-5 4",
  edBreak: "M3 16l6-5M15 8l6-5M8 9l3 3M13 10l3 3",
  edJoin: "M3 17l7-6M14 10l7-5M10 11l4-1M10 11h.01M14 10h.01",
  edReverse: "M4 8h14M14 4l4 4-4 4M20 16H6M10 12l-4 4 4 4",
  edMove: "M12 3v18M3 12h18M12 3l-3 3M12 3l3 3M12 21l-3-3M12 21l3-3M3 12l3-3M3 12l3 3M21 12l-3-3M21 12l-3 3",
  edOffset: "M3 13c3-6 7-6 9-3s6 3 9-3M3 20c3-6 7-6 9-3s6 3 9-3M17 9.5v6",
  edSimplify: "M4 18L20 6M2.4 18a1.6 1.6 0 103.2 0a1.6 1.6 0 10-3.2 0M18.4 6a1.6 1.6 0 103.2 0a1.6 1.6 0 10-3.2 0M8 5.5l4 4M12 5.5l-4 4",
  edDensify: "M3 19L21 5M1.4 19.0a1.6 1.6 0 103.2 0a1.6 1.6 0 10-3.2 0M5.9 15.5a1.6 1.6 0 103.2 0a1.6 1.6 0 10-3.2 0M10.4 12.0a1.6 1.6 0 103.2 0a1.6 1.6 0 10-3.2 0M14.9 8.5a1.6 1.6 0 103.2 0a1.6 1.6 0 10-3.2 0M19.4 5.0a1.6 1.6 0 103.2 0a1.6 1.6 0 10-3.2 0",
  edSmooth: "M3 18C5 6 9 6 12 12s7 6 9-6M3 18h.01M21 6h.01",
  edErase: "M8 20h12M5 15l9-10 6 6-8 9H9z M9.5 10l6 6",
  edSnap: "M7 7h10v10H7zM12 2v5M12 17v5M2 12h5M17 12h5",
  edMore: "M7 10l5 5 5-5",
  // ---- the 3D view toolbar
  vwSection: "M4 20L20 4M4 20h.01M20 4h.01M8 9l3 3M13 14l3 3",
  vwHole: "M12 3v18M9 3h6M6 8l12 8M18 8L6 16",
  vwAuto: "M4 20L20 4M7 4h3M8.5 2.5v3M16 17h4M18 15v4",
  vwClip: "M6 7a2.5 2.5 0 100 5 2.5 2.5 0 000-5zM6 14a2.5 2.5 0 100 5 2.5 2.5 0 000-5zM8 10.5L20 5M8 15.5L20 21",
  vwPrev: "M15 6l-6 6 6 6M5 4v16",
  vwNext: "M9 6l6 6-6 6M19 4v16",
  vwToPlane: "M4 7l8-4 8 4v10l-8 4-8-4zM12 3v18M8 12h8",
  vwMap: "M4 6l5-2 6 2 5-2v14l-5 2-6-2-5 2V6zM6 15L17 8",
  vwReset: "M4 20L20 4M14 14l6 6M20 14l-6 6",
  vwPerspective: "M3 6l8 3v6l-8 3zM11 9l10-5v16l-10-5",
  vwGrid: "M4 4h16v16H4zM4 9.3h16M4 14.7h16M9.3 4v16M14.7 4v16",
  vwVexag: "M12 3v18M8 7l4-4 4 4M8 17l4 4 4-4M4 12h3M17 12h3",
  vwOpacity: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 3v18M12 7h6M12 11h8.6M12 15h8M12 19h4",
} as const;

export type IconName = keyof typeof PATHS;

/** Faces drawn filled: the side views are a cube with its left or right face shaded. */
const FILLS: Partial<Record<IconName, string>> = {
  cubeTop: "M12 3l8 4.5-8 4.5-8-4.5z",
  cubeLeft: "M4 7.5l8 4.5v9l-8-4.5z",
  cubeRight: "M20 7.5l-8 4.5v9l8-4.5z",
};

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {FILLS[name] && <path d={FILLS[name]} fill="currentColor" fillOpacity={0.8} stroke="none" />}
      <path d={PATHS[name]} />
    </svg>
  );
}
