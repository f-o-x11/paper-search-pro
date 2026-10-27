# Shareable report link (opt-in)

Use only when the user explicitly asks for a URL to share the finished report.
The default flow (STEP 14: open `report.html` locally) does not change.

## What it does

`scripts/publish_report.py` uploads `$SEARCH_DIR/report.html` as `index.html`
to [deed.page](https://deed.page/docs), a static host built for agents. No
account or signup is needed: the first publish returns an owner token, which is
saved to `~/.config/deed-page/token` (mode 0600) and reused for later updates.
The token is never printed.

The report bundles its assets (several MB), so the script always uses
deed.page's manifest upload (`POST /v1/uploads` → blob PUT → finalize), which
handles files larger than a single request body.

## Before running

- Tell the user the page will be **public** on a third-party host. Reports can
  contain the user's query and notes; get a yes first.
- `requests` is the only dependency (already used by the helpers).

## Commands

```bash
# publish (prints JSON: ok, url, slug, expires_at)
python -m scripts.publish_report --report "$SEARCH_DIR/report.html"

# temporary link that expires after 1 hour
python -m scripts.publish_report --report "$SEARCH_DIR/report.html" --ttl 3600

# update the same URL after re-rendering (reuses the saved token)
python -m scripts.publish_report --report "$SEARCH_DIR/report.html" --slug <slug>
```

Token lookup order: `DEEDPAGE_TOKEN` env var, then `--token-file`.

## Removing the page

```bash
curl -X DELETE -H "Authorization: Bearer $(cat ~/.config/deed-page/token)" \
  https://deed.page/v1/sites/<slug>
```

## Limits

Free-plan sites show a small "Published with deed.page" link and have size and
site-count limits. If a limit is hit, the script prints the host's
`user_message` verbatim; relay it to the user and do not retry.
