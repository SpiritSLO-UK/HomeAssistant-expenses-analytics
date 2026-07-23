# Scripts

Helper scripts for local development. POSIX `bash` so they run natively on
Linux, macOS and **WSL**, and also under Git Bash on Windows. They auto-detect
the virtualenv layout (`.venv/bin` on Linux/macOS/WSL, `.venv/Scripts` on
Windows) and where `npm` lives.

> Added for **backlog item #8** ("Write test/validation scripts I/you can run
> during changes") from `BACKLOG.md`.

| Script | What it does |
|--------|--------------|
| `test.sh` | Backend pytest (runs across all CPU cores via `pytest-xdist`) + frontend TypeScript type-check. Exits non-zero on any failure - safe for a pre-commit hook or CI. |
| `dev.sh` | Starts the backend (`:8099`) and the Vite dev server (`:5173`) together; Ctrl-C stops both. |
| `functional_test.py` | **End-to-end smoke test against a running instance** (not the unit test DB): health → parsers → import a sample statement → categorise → dashboard → review → service status, with a pass/fail summary + non-zero exit on failure. Dependency-free stdlib (runs on the Pi too). Read-mostly - point it at a standalone/demo instance; `--skip-import` stays read-only. |
| `bump-version.mjs` | **Release only.** Sets the app version in every file that declares it at once (`addon/config.yaml`, `backend/app/__init__.py`, `frontend/package.json` + its lock), so a release bump can't miss one. Node script: `node scripts/bump-version.mjs 1.2.0`. |
| `check-version.mjs` | CI guard: fails if those version files disagree, or (given a tag arg) if they don't match it. Runs in the `frontend` CI job on every PR and in `release.yml` on every tag. `node scripts/check-version.mjs [v1.2.0]`. |

### Releasing: bump the version in one step

The app version lives in a few files that must agree, or the app misreports
itself (this is how the sidebar badge showed `v1.0.2` after the v1.1.0 release -
`addon/config.yaml` was bumped but `package.json` / `__init__.py` were not).
`bump-version.mjs` writes them together and `check-version.mjs` enforces it:

```bash
node scripts/bump-version.mjs 1.2.0    # at release time only (also rewrites addon/config.yaml)
git commit -am "chore(release): v1.2.0"
git tag v1.2.0 && git push --tags       # release.yml re-checks the files match the tag, then builds
```

`check-version.mjs` runs on every PR (inside the required **Frontend** CI check),
so a drift between these files turns the build red before it can merge - no
separate branch-protection entry needed. Between releases the repo holds the last
released version; only bump at release (or when cutting an `-rc`).

```bash
./scripts/test.sh                     # validate the codebase (unit + type-check)
./scripts/dev.sh                      # run the app for development
python scripts/functional_test.py     # smoke-test a running instance (default :8099)
python scripts/functional_test.py --skip-import   # read-only checks
```

`functional_test.py` hits the API directly, so run it against the standalone
`docker compose` instance or a dev server. The Home Assistant add-on serves its
API only through ingress (authenticated) - validate that via the UI checklist in
[`docs/ha-testing.md`](../docs/ha-testing.md).

If the scripts are not executable after cloning:

```bash
chmod +x scripts/*.sh
```

### One-time setup (Linux/macOS/WSL)

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -e 'backend[dev]'
(cd frontend && npm install)
```

CI is live: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs ruff +
the backend pytest suite and the frontend type-check/build on every push and PR
(on Linux it installs all optional extras, so the encryption/MQTT/OCR/PDF paths
run for real), plus a standalone Docker-image build.
