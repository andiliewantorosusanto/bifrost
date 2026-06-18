#!/usr/bin/env bash
# Download the ggml Whisper models Bifröst uses: large-v3 (default) and medium (fallback).
# NEVER download a *turbo* model — turbo cannot do the translate task (§4 of the brief).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/models"
mkdir -p "$DIR"
BASE="https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

for m in large-v3 medium; do
  f="$DIR/ggml-$m.bin"
  if [[ -f "$f" ]]; then
    echo "✓ $f already present"
    continue
  fi
  echo "Downloading ggml-$m.bin …"
  curl -L --fail --progress-bar -o "$f.part" "$BASE/ggml-$m.bin"
  mv "$f.part" "$f"
done

# Silero VAD: lets whisper-server skip music/silence so Whisper never sees it
# (prevents the classic "Thanks for watching" hallucinations on BGM).
VAD="$DIR/ggml-silero-v5.1.2.bin"
if [[ ! -f "$VAD" ]]; then
  echo "Downloading Silero VAD model …"
  curl -L --fail --progress-bar -o "$VAD.part" \
    "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin" \
    && mv "$VAD.part" "$VAD" \
    || echo "VAD download failed — continuing without it (more hallucinations on music)."
fi

echo "Done. Models in $DIR"
