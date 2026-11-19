/**
 * The editor's working copy is plain TypeScript (ADR 0015 §3, ТЗ 7.22 «код редактора не зависит от three.js и
 * React»): no file of `ui/src/edit` — nor any file it reaches by relative imports — imports three or React.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const EDIT = resolve(dirname(fileURLToPath(import.meta.url)), "../../src/edit");
const FORBIDDEN = /^(three|react|react-dom|three-mesh-bvh)(\/|$)|^@react-three\//;
const IMPORT = /(?:^|[\s;])(?:import|export)\s[^'"]*?from\s*["']([^"']+)["']|import\s*\(\s*["']([^"']+)["']\s*\)|^\s*import\s*["']([^"']+)["']/gm;

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? files(p) : /\.tsx?$/.test(name) ? [p] : [];
  });
}

function imports(file: string): string[] {
  const text = readFileSync(file, "utf8");
  const out: string[] = [];
  for (const m of text.matchAll(IMPORT)) out.push(m[1] ?? m[2] ?? m[3]);
  return out;
}

function resolveRelative(from: string, spec: string): string | null {
  const base = resolve(dirname(from), spec);
  for (const p of [base, `${base}.ts`, `${base}.tsx`, join(base, "index.ts")]) {
    try {
      if (statSync(p).isFile()) return p;
    } catch {
      // try the next form
    }
  }
  return null;
}

describe("ui/src/edit is free of three.js and React", () => {
  const own = files(EDIT);

  it("has the modules of the working copy", () => {
    expect(own.length).toBeGreaterThan(0);
    for (const f of own) expect(f.endsWith(".tsx")).toBe(false);
  });

  it("imports neither three nor react, directly or through other modules", () => {
    const seen = new Set<string>();
    const queue = [...own];
    const bad: string[] = [];
    while (queue.length) {
      const file = queue.pop() as string;
      if (seen.has(file)) continue;
      seen.add(file);
      for (const spec of imports(file)) {
        if (FORBIDDEN.test(spec)) bad.push(`${file}: ${spec}`);
        else if (spec.startsWith(".")) {
          const next = resolveRelative(file, spec);
          if (next) queue.push(next);
          else bad.push(`${file}: не найден ${spec}`);
        } else bad.push(`${file}: внешний модуль ${spec}`);
      }
    }
    expect(bad).toEqual([]);
  });

  it("keeps every file within ~600 lines", () => {
    for (const f of own) expect(readFileSync(f, "utf8").split("\n").length, f).toBeLessThanOrEqual(620);
  });
});
