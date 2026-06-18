#!/usr/bin/env bash
# One-command start: native whisper-server (Metal) + the Dockerized app.
# Stop with Ctrl-C (stops both).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [[ ! -f config.toml ]]; then
  echo "No config.toml — copying config.example.toml. Add your API keys to it."
  cp config.example.toml config.toml
fi

# Must exist for the compose bind mount (empty = members-only support disabled).
touch cookies.txt

PORT="${BIFROST_WHISPER_PORT:-8178}"
TRANSLATE_PORT="${BIFROST_TRANSLATE_PORT:-8180}"
TRANSLATE_MODEL="models/${BIFROST_TRANSLATE_MODEL:-Qwen2.5-3B-Instruct-Q4_K_M.gguf}"
WHISPER_PID=""
TRANSLATE_PID=""

cleanup() {
  [[ -n "$WHISPER_PID" ]] && kill "$WHISPER_PID" 2>/dev/null || true
  [[ -n "$TRANSLATE_PID" ]] && kill "$TRANSLATE_PID" 2>/dev/null || true
  docker compose down >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
  echo "✓ whisper-server already running on :$PORT"
else
  echo "Starting whisper-server (Metal, model loads once — large-v3 takes ~10s)…"
  ./whisper/run-whisper-server.sh >/tmp/bifrost-whisper.log 2>&1 &
  WHISPER_PID=$!
  for i in $(seq 1 60); do
    curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1 && break
    if ! kill -0 "$WHISPER_PID" 2>/dev/null; then
      echo "whisper-server failed to start — see /tmp/bifrost-whisper.log" >&2
      exit 1
    fi
    sleep 1
  done
  echo "✓ whisper-server up on :$PORT"
fi

# Local chat translation (optional): only start if the model has been downloaded.
# Without it, chat_translate=auto just keeps using DeepL. Get it via
# llm/download-model.sh (or `make translate-model`).
if curl -fsS "http://127.0.0.1:$TRANSLATE_PORT/health" >/dev/null 2>&1; then
  echo "✓ translate-server already running on :$TRANSLATE_PORT"
elif [[ -f "$TRANSLATE_MODEL" ]]; then
  echo "Starting translate-server (llama.cpp, Metal — local chat translation)…"
  ./llm/run-llm-server.sh >/tmp/bifrost-translate.log 2>&1 &
  TRANSLATE_PID=$!
  for i in $(seq 1 60); do
    curl -fsS "http://127.0.0.1:$TRANSLATE_PORT/health" >/dev/null 2>&1 && break
    if ! kill -0 "$TRANSLATE_PID" 2>/dev/null; then
      echo "translate-server failed to start — see /tmp/bifrost-translate.log" >&2
      TRANSLATE_PID=""; break
    fi
    sleep 1
  done
  [[ -n "$TRANSLATE_PID" ]] && echo "✓ translate-server up on :$TRANSLATE_PORT"
else
  echo "· translate-server skipped (no model — run llm/download-model.sh for offline chat translation)"
fi

echo "Starting Bifröst app container…"
docker compose up --build
