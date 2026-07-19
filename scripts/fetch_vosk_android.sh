#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIBS="$ROOT/android/libs"
AAR="$LIBS/vosk-android-0.3.32.aar"
SO="$LIBS/arm64-v8a/libvosk.so"
URL="https://repo1.maven.org/maven2/com/alphacephei/vosk-android/0.3.32/vosk-android-0.3.32.aar"

mkdir -p "$LIBS/arm64-v8a"

if [[ ! -f "$AAR" ]]; then
  echo "Downloading vosk-android AAR..."
  curl -L -o "$AAR" "$URL"
fi

echo "Extracting libvosk.so for arm64-v8a..."
unzip -jo "$AAR" "jni/arm64-v8a/libvosk.so" -d "$LIBS/arm64-v8a/"

if [[ ! -f "$SO" ]]; then
  echo "Expected native library was not extracted: $SO" >&2
  exit 1
fi

echo "Vosk Android libraries ready:"
echo "  $AAR"
echo "  $SO"
