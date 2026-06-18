#!/usr/bin/env bash
# Download the local chat-translation model (GGUF) for llama-server.
# Default: Qwen2.5-3B-Instruct (Q4_K_M, ~1.9GB) — strong Japanese→English for its
# size and light enough to coexist with whisper on a 16GB M4. Override the file
# with BIFROST_TRANSLATE_MODEL + BIFROST_TRANSLATE_MODEL_URL to use another GGUF.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/models"
mkdir -p "$DIR"

MODEL_NAME="${BIFROST_TRANSLATE_MODEL:-Qwen2.5-3B-Instruct-Q4_K_M.gguf}"
URL="${BIFROST_TRANSLATE_MODEL_URL:-https://huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF/resolve/main/Qwen2.5-3B-Instruct-Q4_K_M.gguf}"
OUT="$DIR/$MODEL_NAME"

if [[ -f "$OUT" ]]; then
  echo "✓ $OUT already present"
  exit 0
fi

echo "Downloading $MODEL_NAME (~1.9GB) …"
curl -L --fail --progress-bar -o "$OUT.part" "$URL"
mv "$OUT.part" "$OUT"
echo "Done. Model in $OUT"
