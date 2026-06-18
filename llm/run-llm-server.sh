#!/usr/bin/env bash
# Start llama-server (llama.cpp, Metal) natively on the host for LOCAL chat
# translation — the same native-service pattern as whisper-server. The container
# reaches it at host.docker.internal:8180. Only chat text is translated here;
# captions are translated by Whisper. Used when chat_translate = local/auto.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_NAME="${BIFROST_TRANSLATE_MODEL:-Qwen2.5-3B-Instruct-Q4_K_M.gguf}"
MODEL="$DIR/models/$MODEL_NAME"
PORT="${BIFROST_TRANSLATE_PORT:-8180}"

if ! command -v llama-server >/dev/null 2>&1; then
  echo "llama-server not found. Install it with: brew install llama.cpp" >&2
  exit 1
fi

if [[ ! -f "$MODEL" ]]; then
  echo "Translation model not found: $MODEL" >&2
  echo "Run llm/download-model.sh first." >&2
  exit 1
fi

# -ngl 99: offload all layers to the Metal GPU. -c 4096: context (chat batches are
# small). --jinja: use the model's chat template. Deterministic output is set
# per-request (temperature 0) by the app.
exec llama-server \
  --model "$MODEL" \
  --host 127.0.0.1 --port "$PORT" \
  -ngl 99 -c 4096 --threads 4 \
  --jinja
