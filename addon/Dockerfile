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

# Install backend + optional extras: SQLCipher (at-rest encryption, backlog #15b),
# paho-mqtt (HA sensors, spec §27) and OCR (pytesseract/Pillow/pypdf, spec §21).
COPY backend/pyproject.toml backend/alembic.ini backend/
COPY backend/app backend/app
RUN pip install --no-cache-dir "./backend[encryption,mqtt,ocr]"

# Bring in the built frontend so app.main can serve it from frontend/dist.
COPY --from=frontend /build/frontend/dist /app/frontend/dist

# Entry script translates HA add-on options into env vars and starts the app.
COPY addon/run.sh /run.sh
RUN chmod +x /run.sh

EXPOSE 8099

CMD ["/run.sh"]
