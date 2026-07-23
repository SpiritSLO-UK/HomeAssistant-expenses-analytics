// CI guard: fail the build if the app version has drifted between the files that
// declare it (addon/config.yaml, backend/app/__init__.py, frontend/package.json
// + its lockfile). This is the "can't forget to bump it everywhere" safety net —
// the v1.1.0 release that left the sidebar showing v1.0.2 would have gone red here.
//
//   node scripts/check-version.mjs           # assert all files agree (PR / main CI)
//   node scripts/check-version.mjs v1.2.0    # also assert they equal this tag (release CI)
//
// Exits 0 when consistent, 1 (with a diff table) otherwise.
import { readVersions } from "./version-files.mjs";

const expected = process.argv[2]?.replace(/^v/, "") || null;
const versions = readVersions();
const distinct = [...new Set(Object.values(versions))];

const consistent = distinct.length === 1;
const matchesTag = !expected || (consistent && distinct[0] === expected);

for (const [file, version] of Object.entries(versions)) {
  const bad = version !== distinct[0] || (expected && version !== expected);
  console.log(`  ${bad ? "✗" : "✓"} ${version.padEnd(14)} ${file}`);
}

if (consistent && matchesTag) {
  const tagNote = expected ? ` (matches tag v${expected})` : "";
  console.log(`\nVersion consistent: ${distinct[0]}${tagNote}`);
  process.exit(0);
}

if (!consistent) {
  console.error(`\n::error::Version drift: files disagree (${distinct.join(", ")}). Run: node scripts/bump-version.mjs <version>`);
} else {
  console.error(`\n::error::Version ${distinct[0]} does not match the release tag v${expected}. Run: node scripts/bump-version.mjs ${expected}`);
}
process.exit(1);
