#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENDOR_DIR="$SCRIPT_DIR/vendor"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SERVER_PORT=8719
VIRTUAL_TIME=8000

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
  cat <<EOF
Usage: $(basename "$0") --input <html-file> [--output <name.pdf>] [--outdir <dir>]

  --input   Path to the HTML file to convert (required)
  --output  Name of the output PDF (default: <input-basename>.pdf)
  --outdir  Directory for the output PDF (default: same as input file)
  --budget  Virtual time budget in ms for JS rendering (default: $VIRTUAL_TIME)
  --help    Show this help

Example:
  $(basename "$0") --input ~/project/Report.html --output report.pdf --outdir ~/Desktop
EOF
  exit 0
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
INPUT=""
OUTPUT=""
OUTDIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input)   INPUT="$2";   shift 2 ;;
    --output)  OUTPUT="$2";  shift 2 ;;
    --outdir)  OUTDIR="$2";  shift 2 ;;
    --budget)  VIRTUAL_TIME="$2"; shift 2 ;;
    --help|-h) usage ;;
    *) echo "Unknown option: $1" >&2; usage ;;
  esac
done

[[ -n "$INPUT" ]] || { echo "ERROR: --input is required" >&2; usage; }

# Resolve absolute paths
INPUT="$(cd "$(dirname "$INPUT")" && pwd)/$(basename "$INPUT")"
[[ -f "$INPUT" ]] || { echo "ERROR: File not found: $INPUT" >&2; exit 1; }

HTML_DIR="$(dirname "$INPUT")"
HTML_FILE="$(basename "$INPUT")"
OUTPUT="${OUTPUT:-${HTML_FILE%.html}.pdf}"
OUTDIR="${OUTDIR:-$HTML_DIR}"
OUTDIR="$(cd "$OUTDIR" 2>/dev/null && pwd || mkdir -p "$OUTDIR" && cd "$OUTDIR" && pwd)"

info() { echo "[export] $*"; }

# ---------------------------------------------------------------------------
# 1. Copy vendor files into the HTML's directory (for the local server)
# ---------------------------------------------------------------------------
VENDOR_CLEANUP=()

copy_vendor_file() {
  local src="$VENDOR_DIR/$1"
  local dst="$HTML_DIR/$1"
  if [[ -f "$src" ]] && [[ ! -f "$dst" ]]; then
    cp "$src" "$dst"
    VENDOR_CLEANUP+=("$dst")
  fi
}

copy_vendor_file "marked.min.js"
copy_vendor_file "purify.min.js"

cleanup_vendor() {
  for f in "${VENDOR_CLEANUP[@]:-}"; do
    [[ -f "$f" ]] && rm -f "$f"
  done
}

# ---------------------------------------------------------------------------
# 2. Kill any stale server on the port
# ---------------------------------------------------------------------------
lsof -ti :"$SERVER_PORT" 2>/dev/null | xargs kill 2>/dev/null || true
sleep 0.3

# ---------------------------------------------------------------------------
# 3. Start local HTTP server
# ---------------------------------------------------------------------------
info "Starting HTTP server on port $SERVER_PORT (serving $HTML_DIR)"
(cd "$HTML_DIR" && python3 -m http.server "$SERVER_PORT" > /dev/null 2>&1) &
SERVER_PID=$!
sleep 1

cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
  cleanup_vendor
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 4. Run headless Chrome
# ---------------------------------------------------------------------------
PDF_PATH="$OUTDIR/$OUTPUT"
info "Exporting: $HTML_FILE → $PDF_PATH"
info "Virtual time budget: ${VIRTUAL_TIME}ms"

"$CHROME" \
  --headless \
  --disable-gpu \
  --no-sandbox \
  --run-all-compositor-stages-before-draw \
  --virtual-time-budget="$VIRTUAL_TIME" \
  --print-to-pdf="$PDF_PATH" \
  --no-pdf-header-footer \
  "http://localhost:${SERVER_PORT}/${HTML_FILE}"

# ---------------------------------------------------------------------------
# 5. Verify output
# ---------------------------------------------------------------------------
if [[ -f "$PDF_PATH" ]]; then
  SIZE=$(ls -lh "$PDF_PATH" | awk '{print $5}')
  info "SUCCESS: $PDF_PATH ($SIZE)"
else
  info "FAILED: PDF was not created."
  exit 1
fi

# ---------------------------------------------------------------------------
# 6. Post-export: run vendor update check
# ---------------------------------------------------------------------------
info "Running vendor update check..."
"$SCRIPT_DIR/check-vendor.sh" || true
