#!/usr/bin/env bash
# Module 17 (environment setup, not part of the learning): a PINNED llama.cpp in .tools/llama.cpp
#   bin/  prebuilt binaries of release $LLAMA_CPP_TAG (llama-server, llama-quantize, ...)
#   src/  the source of the SAME tag (it has convert_hf_to_gguf.py; a converter from another release may not understand your tensors)
# Why not `brew install llama.cpp`: Homebrew follows the newest release, and "newest" changes the flags and the converter under your feet.
# Windows: use WSL2 (Ubuntu) and run this inside it.
set -euo pipefail
TAG="${LLAMA_CPP_TAG:-b12061}"                       # released 2026-11-02
cd "$(dirname "$0")/../.."
DIR="${LLAMA_CPP_DIR:-.tools/llama.cpp}"
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) ASSET="macos-arm64" ;;
  Darwin-x86_64) ASSET="macos-x64" ;;
  Linux-x86_64) ASSET="ubuntu-x64" ;;
  Linux-aarch64|Linux-arm64) ASSET="ubuntu-arm64" ;;
  *) echo "Unsupported platform $(uname -s)-$(uname -m): build llama.cpp $TAG from source (cmake -B build && cmake --build build -j)" >&2; exit 1 ;;
esac
if [ -x "$DIR/bin/llama-server" ] && "$DIR/bin/llama-server" --version 2>&1 | grep -q "${TAG#b}"; then
  echo "llama.cpp $TAG already in $DIR"; exit 0
fi
mkdir -p "$DIR/dl"
curl -fL "https://github.com/ggml-org/llama.cpp/releases/download/$TAG/llama-$TAG-bin-$ASSET.tar.gz" | tar -xz -C "$DIR/dl"
SERVER="$(find "$DIR/dl" -name llama-server -type f | head -1)"
[ -n "$SERVER" ] || { echo "llama-server not found in the archive" >&2; exit 1; }
rm -rf "$DIR/bin"; cp -R "$(dirname "$SERVER")" "$DIR/bin"; rm -rf "$DIR/dl"
if [ ! -d "$DIR/src" ]; then
  git clone --depth 1 --branch "$TAG" https://github.com/ggml-org/llama.cpp "$DIR/src"
fi
"$DIR/bin/llama-server" --version 2>&1 | head -2
echo "ok: $DIR/bin/llama-server, $DIR/src/convert_hf_to_gguf.py"
