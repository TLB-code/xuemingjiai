# Legacy Twitter HTML import

`legacy_html_archive.py` preserves Wayback Machine `text/html` tweet captures
that the main archiver intentionally excludes from its JSON-only CDX query.

For `@zouzoudamowang`, the importer is run from the repository root:

```bash
python legacy_html_archive.py manifest \
  --username zouzoudamowang \
  --account-dir accounts/zouzoudamowang \
  --from-year 2021 --to-year 2026

python legacy_html_archive.py download \
  --username zouzoudamowang \
  --account-dir accounts/zouzoudamowang \
  --from-year 2021 --to-year 2026 \
  --workers 1 --delay 2

python legacy_html_archive.py import \
  --username zouzoudamowang \
  --account-dir accounts/zouzoudamowang \
  --from-year 2021 --to-year 2026
```

The commands are resumable. Raw captures are stored under
`wayback_snapshots/legacy_html/<tweet_id>/<timestamp>.html`. Normalized JSON
and reader-compatible HTML are written to the existing `json/` and `html/`
directories. Run the main index builder from the account directory afterward:

```bash
cd accounts/zouzoudamowang
python ../../archive.py build-index
```

Audit files:

- `legacy_html_manifest.json`: CDX query result.
- `legacy_html_downloads.json`: per-capture download status and checksum.
- `legacy_html_import_report.json`: parse results and unparseable captures.

The importer supports both the old `.tweet-text` DOM and the newer
`article[data-testid="tweet"]` DOM. Original HTML is retained even when a page
cannot be converted for the reader.
