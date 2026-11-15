/**
 * Is a .DAT / .CSV a block model rather than a drillhole table? Decided in the browser from the
 * field names at the start of the file, so the file goes to the object import, not to the
 * drillhole wizard. A block model has a centre (X, Y, Z or XC, YC, ZC) and a size (_X, _Y, _Z or
 * XINC, YINC, ZINC, ...) on all three axes. The server decides again from the whole header.
 */
const CENTRE: Record<string, string[]> = {
  x: ["X", "XC", "XCENTRE", "XCENTER", "X_CENTRE", "EAST", "EASTING", "XMID", "MIDX", "CENTROID_X"],
  y: ["Y", "YC", "YCENTRE", "YCENTER", "Y_CENTRE", "NORTH", "NORTHING", "YMID", "MIDY", "CENTROID_Y"],
  z: ["Z", "ZC", "ZCENTRE", "ZCENTER", "Z_CENTRE", "RL", "ELEV", "ELEVATION", "ZMID", "MIDZ", "CENTROID_Z"],
};
const SIZE: Record<string, string[]> = {
  x: ["_X", "XINC", "DX", "XSIZE", "SIZE_X", "XDIM", "X_SIZE", "DIM_X"],
  y: ["_Y", "YINC", "DY", "YSIZE", "SIZE_Y", "YDIM", "Y_SIZE", "DIM_Y"],
  z: ["_Z", "ZINC", "DZ", "ZSIZE", "SIZE_Z", "ZDIM", "Z_SIZE", "DIM_Z"],
};
const MAGIC = "THIS IS MICROMINE EXTENDED DATA FILE";

export function isBlockModelFields(names: string[]): boolean {
  const upper = new Set(names.map((n) => n.trim().toUpperCase()));
  return (["x", "y", "z"] as const).every((a) => CENTRE[a].some((n) => upper.has(n)) && SIZE[a].some((n) => upper.has(n)));
}

/** Field names from the first bytes of a Micromine DAT (cp1251 or BOCU-1) or a CSV. */
export function fieldNames(head: Uint8Array): string[] {
  const latin = Array.from(head.subarray(0, Math.min(head.length, 1 << 18)), (b) => String.fromCharCode(b)).join("");
  if (latin.startsWith(MAGIC)) {
    const bocu = /codepage="BOCU-1"/i.test(latin.slice(0, 8192));
    const m = /(\d+)\s+VARIABLES\r\n/.exec(latin.slice(4096));
    if (!m) return [];
    const lines = latin.slice(4096 + m.index + m[0].length).split("\r\n").slice(0, Number(m[1]));
    return lines.map((line) => {
      const name = line.replace(/\s+[A-Z]\s+\d+\s+\d+\s*$/, "").trimEnd();
      // BOCU-1 writes ASCII as byte + 0x50 in a fresh field.
      return bocu ? Array.from(name, (ch) => (ch.charCodeAt(0) > 0x70 && ch.charCodeAt(0) < 0xd0 ? String.fromCharCode(ch.charCodeAt(0) - 0x50) : ch)).join("") : name;
    });
  }
  const first = new TextDecoder("utf-8").decode(head.subarray(0, 65536)).split(/\r?\n/)[0] ?? "";
  const sep = [",", ";", "\t", "|"].reduce((best, d) => (first.split(d).length > first.split(best).length ? d : best), ",");
  return first.split(sep).map((c) => c.trim().replace(/^"|"$/g, ""));
}

export async function isBlockModelFile(file: File): Promise<boolean> {
  const s = file.name.toLowerCase();
  if (!(s.endsWith(".dat") || s.endsWith(".csv") || s.endsWith(".txt"))) return false;
  try {
    const head = new Uint8Array(await file.slice(0, 1 << 18).arrayBuffer());
    return isBlockModelFields(fieldNames(head));
  } catch {
    return false;
  }
}
