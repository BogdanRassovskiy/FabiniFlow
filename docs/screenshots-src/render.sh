#!/bin/sh
# Vykreslí ukázkové snímky obrazovky (HTML → PNG) pro simulaci příloh v zákaznickém chatu.
# Použití: sh docs/screenshots-src/render.sh   (vyžaduje Google Chrome)
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
OUT="$SRC/../../web/static/web/screenshots"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
mkdir -p "$OUT"
for name in platba zasilka email; do
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \
    --window-size=390,844 --screenshot="$OUT/$name.png" "file://$SRC/$name.html" >/dev/null 2>&1
  echo "$OUT/$name.png"
done
