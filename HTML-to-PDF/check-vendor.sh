#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENDOR_DIR="$SCRIPT_DIR/vendor"
VERSIONS_FILE="$VENDOR_DIR/versions.json"
LAST_CHECK_FILE="$VENDOR_DIR/.last-update-check"
THROTTLE_SECONDS=86400  # 24 hours
SIZE_THRESHOLD=20       # percent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "[check-vendor] $*"; }

json_field() {
  # Usage: json_field <file> <lib> <field>
  python3 -c "
import json, sys
d = json.load(open('$1'))
print(d['$2']['$3'])
"
}

sha256_of() { shasum -a 256 "$1" | awk '{print $1}'; }

# ---------------------------------------------------------------------------
# Throttle: skip if checked less than 24h ago
# ---------------------------------------------------------------------------
if [[ -f "$LAST_CHECK_FILE" ]]; then
  last_ts=$(cat "$LAST_CHECK_FILE")
  last_epoch=$(date -jf "%Y-%m-%dT%H:%M:%SZ" "$last_ts" +%s 2>/dev/null || echo 0)
  now_epoch=$(date +%s)
  elapsed=$(( now_epoch - last_epoch ))
  if [[ $elapsed -lt $THROTTLE_SECONDS ]]; then
    remaining=$(( (THROTTLE_SECONDS - elapsed) / 3600 ))
    info "Last check was $(( elapsed / 3600 ))h ago (threshold: 24h). Next check in ~${remaining}h. Skipping."
    exit 0
  fi
fi

[[ -f "$VERSIONS_FILE" ]] || die "versions.json not found at $VERSIONS_FILE"

# ---------------------------------------------------------------------------
# Check each library
# ---------------------------------------------------------------------------
any_drift=0
TMPDIR_CHECK=$(mktemp -d)
trap "rm -rf '$TMPDIR_CHECK'" EXIT

for lib in marked dompurify; do
  info "--- Checking $lib ---"

  local_file="$VENDOR_DIR/$(json_field "$VERSIONS_FILE" "$lib" "file")"
  cdn_url="$(json_field "$VERSIONS_FILE" "$lib" "cdn_url")"
  pinned_sha="$(json_field "$VERSIONS_FILE" "$lib" "sha256")"
  pinned_bytes="$(json_field "$VERSIONS_FILE" "$lib" "bytes")"

  [[ -f "$local_file" ]] || { info "  WARNING: local file $local_file missing!"; any_drift=1; continue; }

  # Download CDN copy
  cdn_file="$TMPDIR_CHECK/${lib}_cdn.js"
  if ! curl -sL -o "$cdn_file" "$cdn_url"; then
    info "  WARNING: could not download from CDN ($cdn_url). Skipping."
    continue
  fi

  local_sha=$(sha256_of "$local_file")
  cdn_sha=$(sha256_of "$cdn_file")
  local_bytes=$(wc -c < "$local_file" | tr -d ' ')
  cdn_bytes=$(wc -c < "$cdn_file" | tr -d ' ')

  # Verify local file matches pinned hash
  if [[ "$local_sha" != "$pinned_sha" ]]; then
    info "  WARNING: local file hash does not match versions.json!"
    info "    Expected: $pinned_sha"
    info "    Actual:   $local_sha"
    any_drift=1
  fi

  # Compare local vs CDN
  if [[ "$local_sha" == "$cdn_sha" ]]; then
    info "  IN SYNC (SHA-256 match)"
    info "    Hash: $local_sha"
    info "    Size: $local_bytes bytes"
  else
    any_drift=1
    info "  OUT OF SYNC"
    info "    Local SHA-256: $local_sha ($local_bytes bytes)"
    info "    CDN   SHA-256: $cdn_sha ($cdn_bytes bytes)"

    # Size delta check
    if [[ $local_bytes -gt 0 ]]; then
      delta=$(python3 -c "print(abs($cdn_bytes - $local_bytes) * 100.0 / $local_bytes)")
      delta_int=$(python3 -c "print(int(abs($cdn_bytes - $local_bytes) * 100.0 / $local_bytes))")
      info "    Size delta: ${delta}%"

      if [[ $delta_int -le $SIZE_THRESHOLD ]]; then
        info "    Size is within ${SIZE_THRESHOLD}% threshold."
        info "    SUGGESTION: Update local copy. Run:"
        info "      curl -sL -o \"$local_file\" \"$cdn_url\""
        info "    Then update versions.json with new hash and size."
      else
        info "    WARNING: Size delta exceeds ${SIZE_THRESHOLD}% — this is suspicious."
        info "    The CDN file may be a different major version or compromised."
        info "    Inspect manually before updating."
      fi
    fi
  fi
  echo
done

# ---------------------------------------------------------------------------
# Update timestamp
# ---------------------------------------------------------------------------
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$LAST_CHECK_FILE"
info "Timestamp updated: $(cat "$LAST_CHECK_FILE")"

if [[ $any_drift -eq 0 ]]; then
  info "All vendor files are in sync."
else
  info "One or more vendor files are out of sync. See details above."
fi

exit $any_drift
