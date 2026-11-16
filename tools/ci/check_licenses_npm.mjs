// Fails if a shipped npm package (production closure of ui + electron) is GPL/LGPL/AGPL (ТЗ 6.3).
// Usage: node tools/ci/check_licenses_npm.mjs [-v]
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1")), "..", "..");
const FORBIDDEN = /\b(A|L)?GPL\b/i;
const verbose = process.argv.includes("-v");

function readPkg(dir) {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, "package.json"), "utf8"));
  } catch {
    return null;
  }
}

function resolveDir(name, fromDir) {
  for (let d = fromDir; ; d = path.dirname(d)) {
    const cand = path.join(d, "node_modules", name);
    if (fs.existsSync(path.join(cand, "package.json"))) return cand;
    if (d === path.dirname(d)) return null;
  }
}

const roots = [];
for (const ws of ["ui", "app"]) {
  const pkg = readPkg(path.join(root, ws));
  for (const dep of Object.keys(pkg?.dependencies ?? {})) roots.push([dep, path.join(root, ws)]);
}
roots.push(["electron", path.join(root, "app")]); // shipped runtime

const seen = new Map();
const bad = [];
while (roots.length) {
  const [name, from] = roots.pop();
  const dir = resolveDir(name, from);
  if (!dir || seen.has(dir)) continue;
  const pkg = readPkg(dir);
  const lic = typeof pkg.license === "string" ? pkg.license : JSON.stringify(pkg.license ?? pkg.licenses ?? "UNKNOWN");
  seen.set(dir, `${pkg.name}@${pkg.version}: ${lic}`);
  if (FORBIDDEN.test(lic)) bad.push(seen.get(dir));
  for (const dep of Object.keys(pkg.dependencies ?? {})) roots.push([dep, dir]);
}
if (verbose) console.log([...seen.values()].sort().join("\n"));
if (bad.length) {
  console.error("Forbidden licenses:\n  " + bad.join("\n  "));
  process.exit(1);
}
console.log(`npm licenses ok: ${seen.size} packages`);
