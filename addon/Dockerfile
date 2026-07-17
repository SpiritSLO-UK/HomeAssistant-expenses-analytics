# Multi-stage build for the HA Finance Intelligence add-on.
# Stage 1 builds the React frontend; stage 2 runs the FastAPI backend and
# serves the built frontend. Build context is the repository root.

# ---- Stage 1: build frontend ----
# Base images are pinned digest-only by immutable multi-arch digest (supply-chain
# hardening, CR-FEAT-9); the human-readable tag lives in the comment above each
# FROM (Docker has no inline comments). Refresh the digest when bumping the base.
# node:22-slim
FROM node@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS frontend
WORKDIR /build/frontend
# Copy the lockfile explicitly (no glob) so a missing lock fails the build loudly,
# and use `npm ci` for a clean, reproducible install of the locked, CI-verified tree.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 1b: build the sqlcipher3 wheel for arm64 ----
# amd64 gets at-rest encryption from the prebuilt `sqlcipher3-binary` wheel (the
# `encryption` extra). arm64 has no such wheel, so compile the `sqlcipher3` sdist
# against Debian's SQLCipher here, in a throwaway stage, and copy just the
# resulting wheel into the runtime — keeping the build toolchain out of the final
# image. On amd64 this stage is a no-op (empty /wheels).
# python:3.12-slim
FROM python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS sqlcipher
ARG TARGETARCH
RUN mkdir -p /wheels \
    && if [ "$TARGETARCH" = "arm64" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends build-essential libsqlcipher-dev python3-dev \
        && pip wheel --no-cache-dir --wheel-dir /wheels "sqlcipher3>=0.5" \
        && rm -rf /var/lib/apt/lists/* ; \
    fi

# ---- Stage 2: backend runtime ----
# python:3.12-slim
FROM python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS runtime
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HAFI_DATABASE_PATH=/data/finance/finance.db

# Tesseract for local receipt OCR (spec §21; the "ocr" extra needs the binary).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Install backend + optional extras: paho-mqtt (HA sensors, spec §27) and OCR
# (pytesseract/Pillow/pypdf, spec §21) on every arch. SQLCipher at-rest encryption
# (backlog #15b) ships a prebuilt wheel for amd64 only via the `encryption` extra
# (`sqlcipher3-binary`). aarch64 has no such wheel, so it gets at-rest encryption
# from the `sqlcipher3` wheel compiled in the `sqlcipher` stage above (installed
# below, with the runtime SQLCipher lib). TARGETARCH is provided automatically by
# buildx (amd64 / arm64); a plain `docker build` on amd64 leaves it amd64.
ARG TARGETARCH
COPY backend/pyproject.toml backend/alembic.ini backend/
COPY backend/app backend/app
RUN EXTRAS="mqtt,ocr" \
    && if [ "$TARGETARCH" = "amd64" ]; then EXTRAS="encryption,$EXTRAS"; fi \
    && echo "Installing backend extras: [$EXTRAS] (TARGETARCH=$TARGETARCH)" \
    && pip install --no-cache-dir "./backend[$EXTRAS]"

# aarch64: install the locally-built sqlcipher3 wheel + the runtime SQLCipher
# library, so at-rest encryption works on the Pi too (amd64 already has it above).
COPY --from=sqlcipher /wheels /tmp/wheels
# The runtime SQLCipher lib's package name is Debian-release-specific: trixie ships
# libsqlcipher1, bookworm shipped libsqlcipher0 — try the newer first, fall back.
RUN if [ "$TARGETARCH" = "arm64" ]; then \
        apt-get update \
        && (apt-get install -y --no-install-recommends libsqlcipher1 \
            || apt-get install -y --no-install-recommends libsqlcipher0) \
        && rm -rf /var/lib/apt/lists/* \
        && pip install --no-cache-dir --only-binary=:all: /tmp/wheels/sqlcipher3*.whl ; \
    fi \
    && rm -rf /tmp/wheels

# Bring in the built frontend so app.main can serve it from frontend/dist.
COPY --from=frontend /build/frontend/dist /app/frontend/dist

# Entry script translates HA add-on options into env vars and starts the app.
COPY addon/run.sh /run.sh

# Run unprivileged (CR-SEC-7): the app, Tesseract and the web server all run as a
# non-root system user instead of UID 0. /data holds the SQLite finance DB and is
# a runtime volume, so create + own it at build time — a fresh Docker volume (the
# standalone `docker compose` / CI path) inherits this ownership and stays
# writable by the app user. /app is chowned too so anything the app touches under
# its tree is writable. The entrypoint is made executable in the same layer.
# ⚠️ Needs on-Pi verification before release: Home Assistant's Supervisor mounts
# /data itself, and its runtime ownership must let UID 10001 write (see PR notes).
RUN chmod +x /run.sh \
    && useradd --system --uid 10001 --create-home --home-dir /home/appuser appuser \
    && mkdir -p /data/finance \
    && chown -R 10001:10001 /data /app
USER 10001

EXPOSE 8099

# Report container health from the unauthenticated /api/health endpoint so
# orchestrators (Docker / compose / HA) can detect a wedged process (CR-FEAT-2).
# Generous start period to cover first-run migrations on slow hardware (e.g. a Pi).
# `docker compose` inherits this image HEALTHCHECK, so both run paths are covered.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python", "-c", "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8099/api/health', timeout=4).status == 200 else 1)"]

CMD ["/run.sh"]
