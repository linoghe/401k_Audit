# HTML-to-PDF Export Tool

A self-contained tool that converts any local HTML file to a pageless PDF using headless Google Chrome.

## Requirements

- macOS (tested on macOS 15+)
- Google Chrome (installed at `/Applications/Google Chrome.app`)
- Python 3 (for the local HTTP server)
- `curl`, `shasum` (pre-installed on macOS)

## Quick start

```bash
~/Documents/lino-apps/HTML-to-PDF/export.sh \
  --input /path/to/Report.html \
  --output report.pdf \
  --outdir ~/Desktop
```

### Arguments

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--input` | Yes | — | Path to the HTML file |
| `--output` | No | `<input-name>.pdf` | Output PDF filename |
| `--outdir` | No | Same directory as input | Where to save the PDF |
| `--budget` | No | `8000` | Virtual time budget (ms) for JS rendering |

## How it works

### Export flow

1. **Copies vendor JS files** (`marked.min.js`, `purify.min.js`) into the HTML file's directory temporarily, so the local server can serve them alongside the HTML.
2. **Starts a local HTTP server** (`python3 -m http.server`) on port 8719, serving the HTML's parent directory. This is necessary because Chrome blocks `fetch()` over `file://` protocol.
3. **Launches headless Chrome** with `--print-to-pdf`, pointing at `http://localhost:8719/<file>`. The `--virtual-time-budget` flag gives JavaScript time to fetch data and render before Chrome captures the page.
4. **Captures the PDF**. If the HTML includes an `@page` CSS rule sized to the content height (pageless mode), the PDF will be a single continuous page with no breaks.
5. **Cleans up**: kills the HTTP server, removes the temporary vendor file copies.
6. **Runs the vendor update check** (see below).

### Pageless PDF support

For the PDF to be a single continuous page (no page breaks, no blank bottom), the HTML needs either:

- A static CSS rule: `@page { size: 8.5in <tall>in; margin: 0; }`
- Or a JS snippet that measures `document.documentElement.scrollHeight` after render and injects the `@page` rule dynamically

Without this, Chrome uses its default Letter page size with pagination.

## Vendored libraries

Two JavaScript libraries are bundled locally in `vendor/`:

- **marked.js** v11.1.1 — Markdown to HTML conversion
- **DOMPurify** v3.0.8 — HTML sanitization

### Why vendor them?

- **Speed**: No CDN round-trip during PDF export.
- **Reliability**: Export works offline and doesn't depend on jsDelivr availability.
- **Reproducibility**: Pinned versions ensure consistent rendering.

### Fallback loading pattern

When updating an HTML file to use this tool's vendor files, use this pattern in `<script>` tags:

```html
<!-- Local first, CDN fallback -->
<script src="marked.min.js"></script>
<script>
  if (typeof marked === 'undefined') {
    document.write('<script src="https://cdn.jsdelivr.net/npm/marked@11.1.1/marked.min.js"><\/script>');
  }
</script>
<script src="purify.min.js"></script>
<script>
  if (typeof DOMPurify === 'undefined') {
    document.write('<script src="https://cdn.jsdelivr.net/npm/dompurify@3.0.8/dist/purify.min.js"><\/script>');
  }
</script>
```

During export, `export.sh` copies the vendor files into the HTML's directory so the local `src="marked.min.js"` paths resolve. For browser viewing (GitHub Pages, etc.), the CDN fallback handles it.

## Vendor update protocol

After every successful PDF export, `check-vendor.sh` runs automatically. It is throttled to run at most once every 24 hours.

### What it checks

1. Downloads the pinned CDN versions to temp files
2. Computes SHA-256 hashes of both local and CDN copies
3. Compares hashes — reports "in sync" or "out of sync"
4. If out of sync, checks file size delta:
   - **Within 20%**: Suggests updating the local copy
   - **Beyond 20%**: Flags as suspicious (possible major version change or compromised package)
5. Updates the `.last-update-check` timestamp

### Running manually

```bash
~/Documents/lino-apps/HTML-to-PDF/check-vendor.sh
```

To force a check (bypass the 24h throttle), delete the timestamp file:

```bash
rm ~/Documents/lino-apps/HTML-to-PDF/vendor/.last-update-check
~/Documents/lino-apps/HTML-to-PDF/check-vendor.sh
```

### Version policy

- **Minor/patch updates**: Safe to apply when `check-vendor.sh` suggests it.
- **Major version bumps**: Inspect manually. Major versions may change APIs or output behavior. Update the `cdn_url` in `versions.json` to point at the new version, then re-download.

### Updating a vendor file

```bash
cd ~/Documents/lino-apps/HTML-to-PDF/vendor
curl -sL -o marked.min.js "https://cdn.jsdelivr.net/npm/marked@<NEW_VERSION>/marked.min.js"
shasum -a 256 marked.min.js    # Copy this hash
wc -c < marked.min.js          # Copy this byte count
# Update versions.json with new version, hash, bytes, and download date
```

## File structure

```
HTML-to-PDF/
  README.md              This file
  export.sh              Main export script
  check-vendor.sh        Vendor update checker
  vendor/
    marked.min.js        Pinned local copy of marked.js
    purify.min.js        Pinned local copy of DOMPurify
    versions.json        Version metadata (version, SHA-256, size, date)
    .last-update-check   Timestamp of last CDN comparison
```

## Cursor AI agent integration

A Cursor rule at `~/.cursor/rules/html-to-pdf.mdc` points the AI agent to this README. When you ask the agent to export HTML to PDF, it reads this file and follows the procedure.
