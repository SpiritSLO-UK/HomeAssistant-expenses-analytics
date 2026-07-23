// Single source of truth for WHERE the app version lives.
//
// The app version is duplicated across a handful of files that MUST stay in
// lockstep or the app misreports itself (this is exactly how the sidebar badge
// drifted to v1.0.2 after the v1.1.0 release — addon/config.yaml was bumped but
// package.json / __init__.py were not). Both the release bump tool
// (bump-version.mjs) and the CI consistency guard (check-version.mjs) import
// this module, so they can never disagree about the set of files or how to
// read/write each one.
//
// addon/config.yaml is the release source of truth: Home Assistant's Supervisor
// pulls `<image>:<version>`, so its `version:` must equal the git tag. The
// others exist only to report the running version to the UI/API and must track
// it. Per the release process, addon/config.yaml is bumped ONLY at release.
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

// Preserve each file's existing style (2-space indent + trailing newline) so a
// bump produces a minimal, reviewable diff rather than reformatting churn.
function bumpJson(source, version, extra) {
  const data = JSON.parse(source);
  data.version = version;
  extra?.(data, version);
  return JSON.stringify(data, null, 2) + "\n";
}

// Each entry knows how to read the current version out of one file and how to
// write a new one back, touching only the version so the rest is untouched.
const FILES = {
  "addon/config.yaml": {
    // Linear patterns (no lazy quantifier between optional quotes / no \s*.* overlap)
    // to avoid the super-linear backtracking Sonar flags (S5852).
    read: (s) => s.match(/^version:\s*"?([^\s"]+)/m)?.[1],
    write: (s, v) => s.replace(/^version:[^\n]*$/m, `version: "${v}"`),
  },
  "backend/app/__init__.py": {
    read: (s) => s.match(/__version__\s*=\s*"([^"]+)"/)?.[1],
    write: (s, v) => s.replace(/(__version__\s*=\s*")[^"]+(")/, `$1${v}$2`),
  },
  "frontend/package.json": {
    read: (s) => JSON.parse(s).version,
    write: (s, v) => bumpJson(s, v),
  },
  // package-lock mirrors package.json's version in two places (root + the
  // "" root-package entry); keep both in sync so `npm ci` sees a matching lock.
  "frontend/package-lock.json": {
    read: (s) => JSON.parse(s).version,
    write: (s, v) => bumpJson(s, v, (data) => {
      if (data.packages?.[""]) data.packages[""].version = v;
    }),
  },
};

export const VERSION_FILES = Object.keys(FILES);

/** Read the declared version from every tracked file → { relPath: version }. */
export function readVersions() {
  const out = {};
  for (const [rel, spec] of Object.entries(FILES)) {
    const version = spec.read(readFileSync(join(ROOT, rel), "utf8"));
    if (!version) throw new Error(`Could not find a version in ${rel}`);
    out[rel] = version.trim();
  }
  return out;
}

/** Write `version` into every tracked file. Returns the list of files touched. */
export function writeVersion(version) {
  for (const [rel, spec] of Object.entries(FILES)) {
    const path = join(ROOT, rel);
    writeFileSync(path, spec.write(readFileSync(path, "utf8"), version));
  }
  return VERSION_FILES;
}
