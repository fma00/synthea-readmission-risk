#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

JAR_URL="https://github.com/synthetichealth/synthea/releases/download/v4.0.0/synthea-with-dependencies.jar"
JAR_PATH="tools/synthea/synthea-with-dependencies.jar"
# Sourced directly from GitHub's Releases API digest field for this exact asset
# (curl https://api.github.com/repos/synthetichealth/synthea/releases/tags/v4.0.0) -- an
# authoritative, independently-computed checksum, not a value trusted from whichever machine
# runs this script first.
EXPECTED_SHA256="ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1"

mkdir -p tools/synthea

if [ ! -f "$JAR_PATH" ]; then
    echo "Downloading Synthea v4.0.0..."
    curl -fL "$JAR_URL" -o "$JAR_PATH" || {
        rm -f "$JAR_PATH"
        echo "ERROR: download failed" >&2
        exit 1
    }
fi

COMPUTED_HASH=$(shasum -a 256 "$JAR_PATH" | cut -d' ' -f1)

if [ "$COMPUTED_HASH" != "$EXPECTED_SHA256" ]; then
    rm -f "$JAR_PATH"
    echo "ERROR: jar hash mismatch. Expected $EXPECTED_SHA256, got $COMPUTED_HASH" >&2
    exit 1
fi

echo "Synthea jar verified at $JAR_PATH"
