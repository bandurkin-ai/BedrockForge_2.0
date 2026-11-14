/** Line icons on a 24px grid; they take the text colour. */
const PATHS = {
  cube: "M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zM4 7.5l8 4.5 8-4.5M12 12v9",
  plan: "M4 6l5-2 6 2 5-2v14l-5 2-6-2-5 2V6zM9 4v14M15 6v14",
  section: "M3 18h18M3 18V8l5 3 4-5 4 6 5-3v9M8 11v7M16 12v6",
  log: "M9 3h6v18H9zM9 8h6M9 12h6M9 17h6M5 3v18",
  table: "M3 5h18v14H3zM3 10h18M3 14.5h18M9 5v14",
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
  edit: "M4 20h4L19 9l-4-4L4 16v4zM13 7l4 4",
  chevronRight: "M9 6l6 6-6 6",
  lines: "M3 17l5-6 4 3 4-7 5 5",
  wireframe: "M12 3l8 5v8l-8 5-8-5V8l8-5zM4 8l8 5 8-5M12 13v8M8 5.5l8 5M16 5.5l-8 5",
  blocks: "M3 8l5-3 5 3v6l-5 3-5-3V8zM13 11l4-2.5 4 2.5v5l-4 2.5-4-2.5M8 11v6",
  raster: "M4 5h16v14H4zM4 15l4-4 4 4 3-3 5 5M15 9.5a1 1 0 100-.1",
  drillholes: "M7 3v18M17 3l-4 18M12 3v12M4 3h16",
  section2: "M4 20L20 4M7 20L20 7M4 17L17 4",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg
      className="icon-svg"
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
      <path d={PATHS[name]} />
    </svg>
  );
}
