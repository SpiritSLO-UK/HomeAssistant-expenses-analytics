// Release helper: set the app version in every file that declares it, in one go,
// so the release bump can't miss one (which is how the sidebar drifted to v1.0.2).
//
//   node scripts/bump-version.mjs 1.2.0
//   node scripts/bump-version.mjs 1.2.0-rc1
//
// Run this at release time, commit the result (chore(release): vX.Y.Z), then tag.
// The tag build re-checks the files match the tag via check-version.mjs. Note
// this DOES rewrite addon/config.yaml, which the release process says to touch
// only at release — so only run this when cutting a release (or an rc).
import { writeVersion, readVersions } from "./version-files.mjs";

const version = (process.argv[2] || "").replace(/^v/, "");

// Semantic version with an optional prerelease suffix (e.g. 1.2.0, 1.2.0-rc1).
if (!/^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$/.test(version)) {
  console.error(`Usage: node scripts/bump-version.mjs <version>   (e.g. 1.2.0 or 1.2.0-rc1)\nGot: "${process.argv[2] ?? ""}"`);
  process.exit(1);
}

const before = readVersions();
const files = writeVersion(version);

console.log(`Bumped to ${version}:`);
for (const file of files) console.log(`  ${before[file]} -> ${version}  ${file}`);
console.log(`\nNext: commit (chore(release): v${version}), then tag v${version} to build + publish.`);
