# Scripts

Helper scripts for local development. POSIX `bash` so they run natively on
Linux, macOS and **WSL**, and also under Git Bash on Windows. They auto-detect
the virtualenv layout (`.venv/bin` on Linux/macOS/WSL, `.venv/Scripts` on
Windows) and where `npm` lives.

> Added for **backlog item #8** ("Write test/validation scripts I/you can run
> during changes") from `things-to-add-change-consider.md`.

| Script | What it does |
|--------|--------------|
| `test.sh` | Backend pytest + frontend TypeScript type-check. Exits non-zero on any failure — safe for a pre-commit hook or CI. |
| `dev.sh` | Starts the backend (`:8099`) and the Vite dev server (`:5173`) together; Ctrl-C stops both. |

```bash
./scripts/test.sh      # validate everything
./scripts/dev.sh       # run the app for development
```

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

A GitHub Actions workflow that runs `test.sh` is planned for Stage 12
(open-source polish).
