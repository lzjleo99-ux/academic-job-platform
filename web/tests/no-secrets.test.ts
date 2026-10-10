// T15 acceptance: "no secrets/API keys in client bundle (guard test)". The listing API is read-only and
// needs no key, so nothing under app/components/lib should ever reference one; this scans the actual source
// that ships to the client (everything is a Client Component or imported by one - see app/layout.tsx) for the
// shapes a real credential takes, and separately checks the production build output once it exists.
import fs from "fs";
import path from "path";
import { describe, expect, it } from "vitest";

const ROOT = path.resolve(__dirname, "..");
const SOURCE_DIRS = ["app", "components", "lib"];

const SECRET_PATTERNS: [string, RegExp][] = [
  ["OpenAI-style key", /sk-[A-Za-z0-9]{20,}/],
  ["AWS access key", /AKIA[0-9A-Z]{16}/],
  ["Postgres connection string", /postgres(?:ql)?:\/\/[^\s"'`]*:[^\s"'`]*@/],
  ["generic 'secret ='/'api_key ='", /\b(secret|api[_-]?key|password)\s*[:=]\s*["'][^"'\s]{8,}["']/i],
  ["private key block", /-----BEGIN (RSA |EC )?PRIVATE KEY-----/],
];
// process.env.X is fine only when X is a NEXT_PUBLIC_ var (those are meant to reach the client) or a
// build-time-only Node var never bundled (none of those are read anywhere in this app - see lib/api.ts).
const NON_PUBLIC_ENV = /process\.env\.(?!NEXT_PUBLIC_)[A-Z_][A-Z0-9_]*/;

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(p));
    else if (/\.(ts|tsx|js|jsx)$/.test(entry.name)) out.push(p);
  }
  return out;
}

const files = SOURCE_DIRS.flatMap((d) => walk(path.join(ROOT, d)));

describe("no secrets in the client source", () => {
  it.each(files)("%s has no credential-shaped string", (file) => {
    const content = fs.readFileSync(file, "utf-8");
    for (const [label, re] of SECRET_PATTERNS) {
      expect(re.test(content), `${label} pattern matched in ${file}`).toBe(false);
    }
  });

  it.each(files)("%s never reads a non-NEXT_PUBLIC_ env var", (file) => {
    const content = fs.readFileSync(file, "utf-8");
    const m = content.match(NON_PUBLIC_ENV);
    expect(m, `${file} reads ${m?.[0]} - only NEXT_PUBLIC_* vars are safe in client code`).toBeNull();
  });

  it("the only external base URL is the configurable, public, localhost-by-default API base", () => {
    const apiFile = fs.readFileSync(path.join(ROOT, "lib/api.ts"), "utf-8");
    expect(apiFile).toContain("NEXT_PUBLIC_API_BASE");
    expect(apiFile).toContain("http://127.0.0.1:8000");
  });
});

describe("production build output (skips if not built yet - run `npm run build` first for full coverage)", () => {
  const nextDir = path.join(ROOT, ".next", "static");
  const built = fs.existsSync(nextDir);

  it.skipIf(!built)("no client JS chunk contains a credential-shaped string", () => {
    const jsFiles = walk(nextDir).filter((f) => f.endsWith(".js"));
    expect(jsFiles.length).toBeGreaterThan(0);
    for (const file of jsFiles) {
      const content = fs.readFileSync(file, "utf-8");
      for (const [label, re] of SECRET_PATTERNS) {
        expect(re.test(content), `${label} pattern matched in built chunk ${file}`).toBe(false);
      }
    }
  });
});
