#!/bin/sh
# Download the Freerouting autorouter used by hardware/gen/build_pcb.py
set -e
cd "$(dirname "$0")"
curl -fL -o freerouting-1.9.0.jar https://github.com/freerouting/freerouting/releases/download/v1.9.0/freerouting-1.9.0.jar
echo "ok: $(pwd)/freerouting-1.9.0.jar"
