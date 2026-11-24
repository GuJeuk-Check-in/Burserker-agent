# Private local export reconciliation

`oijwef098234.gujeuk_ai.export_reconcile` accepts only explicitly named local JSON files. It does not crawl a directory, call Notion, or upload an export. Use synthetic files for development and tests; do not point the current workflow at real Downloads or private team exports.

The caller supplies a separate trusted page ID list and an approval manifest. Every approved field and row field needs a SHA-256 hash of its publicly observed UTF-8 value and the public observation time, which must be at least as recent as the export page's modification time. A page ID alone never approves fields or rows. Unlisted fields and rows are omitted. Credential-shaped content, contact information, personal identifiers, and person-related fields cause rejection. Any invalid input stops reconciliation before database changes.

The named page files use this shape:

```json
{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","modified_at":"2025-01-02T00:00:00Z","fields":{"Title":"Public plan"},"rows":[{"id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","fields":{"Task":"Public task"}}]}
```

The approval file uses this shape. Its hashes must come from separately reviewed public field and row evidence:

```json
{"version":1,"pages":[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","url":"https://magical-sovereign-7b8.notion.site/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","file":"page.json","fields":{"Title":{"sha256":"<SHA-256 of Public plan>","observed_at":"2025-01-02T00:00:00Z"}},"rows":[{"id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","fields":{"Task":{"sha256":"<SHA-256 of Public task>","observed_at":"2025-01-02T00:00:00Z"}}}]}]}
```

For a synthetic fixture, run `PYTHONPATH=src .venv/bin/python -m oijwef098234.gujeuk_ai.export_reconcile --export-root FIXTURE_DIR --approval APPROVAL.json --stage PRIVATE_STAGE --state LOCAL_WORKSPACE.db --trusted-page-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`. The stage directory is mode `0700`; its sanitized `approved.json` is mode `0600`. The workspace stores approved slices only. Changed slices enter the existing wiki digest queue. Repeating an unchanged reconciliation does not enqueue work. Withdrawing approval removes the corresponding local export document so its wiki claims stop appearing and the next render retracts its page. Coverage remains explicitly incomplete; public export evidence cannot establish hidden fields, unlisted rows, or current access beyond its observation time.
