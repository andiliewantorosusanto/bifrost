# Bifröst app container: FastAPI orchestrator + UI + yt-dlp + ffmpeg + chat/translate.
# Whisper is NOT in here — it needs Metal, so whisper-server runs natively on the
# host and the container reaches it at host.docker.internal (see docker-compose.yml).

# Stage 1: build the frontend (Vite + React + TypeScript + Tailwind).
FROM node:22-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ .
RUN npm run build

# Stage 2: the app.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Deno: JS runtime for yt-dlp's YouTube n-challenge solver. Without it YouTube
# throttles downloads to ~KB/s, far below real-time.
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /srv/bifrost

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r backend/requirements.txt

COPY backend backend
COPY --from=frontend /fe/dist frontend

ENV BIFROST_FRONTEND=/srv/bifrost/frontend \
    BIFROST_CONFIG=/srv/bifrost/config.toml \
    PYTHONUNBUFFERED=1

EXPOSE 7842
WORKDIR /srv/bifrost/backend
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7842"]
