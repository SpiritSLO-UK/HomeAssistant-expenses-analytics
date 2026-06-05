# Multi-stage build for the HA Finance Intelligence add-on.
# Stage 1 builds the React frontend; stage 2 runs the FastAPI backend and
# serves the built frontend. Build context is the repository root.

# ---- Stage 1: build frontend ----
FROM node:22-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---- Stage 1b: build the sqlcipher3 wheel for arm64 ----
# amd64 gets at-rest encryption from the prebuilt `sqlcipher3-binary` wheel (the
# `encryption` extra). arm64 has no such wheel, so compile the `sqlcipher3` sdist
# against Debian's SQLCipher here, in a throwaway stage, and copy just the
# resulting wheel into the runtime — keeping the build toolchain out of the final
# image. On amd64 this stage is a no-op (empty /wheels).
FROM python:3.12-slim AS sqlcipher
ARG TARGETARCH
RUN mkdir -p /wheels \
    && if [ "$TARGETARCH" = "arm64" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends build-essential libsqlcipher-dev python3-dev \
        && pip wheel --no-cache-dir --wheel-dir /wheels "sqlcipher3>=0.5" \
        && rm -rf /var/lib/apt/lists/* ; \
    fi

# ---- Stage 2: backend runtime ----
FROM python:3.12-slim AS runtime
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
RUN chmod +x /run.sh

EXPOSE 8099

CMD ["/run.sh"]
