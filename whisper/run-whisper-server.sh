#!/usr/bin/env bash
# Start whisper-server (whisper.cpp, Metal) natively on the host.
# The model is loaded ONCE here and reused for every chunk.
# Model comes from config.toml (whisper_model) or $BIFROST_WHISPER_MODEL.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_NAME="${BIFROST_WHISPER_MODEL:-$(sed -n 's/^whisper_model *= *"\(.*\)".*/\1/p' "$DIR/config.toml" 2>/dev/null || true)}"
MODEL_NAME="${MODEL_NAME:-large-v3}"
MODEL="$DIR/models/ggml-${MODEL_NAME}.bin"
PORT="${BIFROST_WHISPER_PORT:-8178}"

if [[ "$MODEL_NAME" == *turbo* ]]; then
  echo "Refusing to load '$MODEL_NAME': turbo models cannot translate (they echo the source language)." >&2
  exit 1
fi

if [[ ! -f "$MODEL" ]]; then
  echo "Model not found: $MODEL" >&2
  echo "Run whisper/download-models.sh first." >&2
  exit 1
fi

# Voice-activity detection: skip music/silence before it reaches Whisper —
# prevents hallucinated phrases ("Thanks for watching") on non-speech chunks.
VAD_MODEL="$DIR/models/ggml-silero-v5.1.2.bin"
VAD_ARGS=()
if [[ -f "$VAD_MODEL" ]]; then
  VAD_ARGS=(--vad --vad-model "$VAD_MODEL")
else
  echo "Note: no VAD model ($VAD_MODEL) — run whisper/download-models.sh to reduce music hallucinations." >&2
fi

# --translate: Whisper's translate task — output is always English (the point of Bifröst).
# -l auto: detect the source language (primarily Japanese, but anything works).
# --suppress-nst: drop non-speech tokens (♪ etc.).
exec whisper-server \
  --model "$MODEL" \
  --host 127.0.0.1 --port "$PORT" \
  --translate --language auto \
  --suppress-nst \
  "${VAD_ARGS[@]}" \
  --threads 4
